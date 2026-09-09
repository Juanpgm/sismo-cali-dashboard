"""Blob-persisted incremental state around
`app.services.reportes_panel_match`'s grid-indexed spatial join.

Why incrementality matters as much as the grid itself: the grid index
(`reportes_panel_match.build_grid`/`nearest_in_grid`) bounds each candidato's
comparison to its own neighborhood instead of the whole Panel, but that alone
still means re-matching every citizen report on every refresh. The OTHER
half of keeping this cheap is never re-running the geo/address cascade for a
reporte that was already resolved in a previous run — `select_candidatos`
below is the pure filter that makes that possible, and `apply_panel_match`
is the orchestration that persists the running result to Vercel Blob (never
Firestore — this project's Firestore usage is already quota-sensitive, see
`app.routers.sticker_status`'s docstring about the 31-ago-2026 429 outage)
so the NEXT refresh only ever pays the grid-join cost for genuinely new
reportes.

The sticker flag is deliberately NOT part of that persisted state: a Panel
point can gain a field sticker after a reporte was already matched to it, so
`apply_panel_match` recomputes `sticker` from a fresh
`load_sticker_coverage()` (cheap Set membership) on every call, while the
expensive `panel_registro_id`/`tier`/`dist_m` match itself is only ever
computed once per reporte.
"""
from __future__ import annotations

import logging

from app.services import blob_lkg
from app.services import reportes_panel_match

PANEL_MATCH_STATE_BLOB = "data/reportes_panel_match_state.json"
# Reuses routers/sticker_status.py's STICKER_STATUS_LKG_BLOB pathname
# (kept as a literal here — importing the router module from a service just
# for a string constant isn't worth the coupling).
STICKER_STATUS_BLOB = "data/sticker_status_last_good.json"

# Bounds the worst-case per-run cost of apply_panel_match. select_candidatos
# returns EVERY reporte not yet matched — including every reporte that has
# NEVER matched, by design (see select_candidatos' own docstring) — and a
# geo miss falls through to cruce_gestor.match_by_direccion, a full linear
# SequenceMatcher scan of the whole Panel address index (~O(roster_sin_geo ×
# evaluaciones)). With hundreds-to-thousands of permanently-unmatched
# reportes (very plausible — most citizen self-reports have no corresponding
# EDE survey point), handing the WHOLE backlog to cross_reference every run
# can take minutes, synchronously. Slicing here spreads that cost across
# many runs — the reportes past the cap simply remain candidates for the
# NEXT run, exactly like an unmatched reporte already does — same "bounded
# work per run" philosophy reportes_historico.py's backfill chunking
# already established. ~300 * ~300ms worst-case address-fallback ≈ 90s.
PANEL_MATCH_MAX_CANDIDATES_PER_RUN = 300


# ── Blob I/O, parameterized so apply_panel_match can inject a fake without
# monkeypatching the module-level `blob_lkg` reference (see its own kwargs).
def _load_state(blob_lkg_module) -> dict:
    data = blob_lkg_module.load_json(PANEL_MATCH_STATE_BLOB, dict)
    if not isinstance(data, dict) or not isinstance(data.get("matches"), dict):
        return {"matches": {}}
    return data


def _save_state(blob_lkg_module, state: dict) -> None:
    blob_lkg_module.save_json(PANEL_MATCH_STATE_BLOB, state)


def _load_sticker_coverage(blob_lkg_module) -> set[str]:
    data = blob_lkg_module.load_json(STICKER_STATUS_BLOB, dict)
    if not isinstance(data, dict):
        return set()
    con_sticker = data.get("con_sticker")
    if not isinstance(con_sticker, list):
        return set()
    return {str(x) for x in con_sticker}


def load_state() -> dict:
    """`{'matches': {reporte_id: {'panel_registro_id', 'tier', 'dist_m'}}}`.
    Fails soft to `{'matches': {}}` (first-run shape) on ANY problem —
    missing Blob, malformed JSON, or a payload with the wrong shape
    (`blob_lkg.load_json` itself already guards the first two; the
    'matches' key check here guards the third)."""
    return _load_state(blob_lkg)


def save_state(state: dict) -> None:
    """Fire-and-forget persist via `blob_lkg.save_json` — never raises,
    matching `blob_lkg`'s own contract."""
    _save_state(blob_lkg, state)


def load_sticker_coverage() -> set[str]:
    """Set of Panel `registro_id`s with a confirmed field sticker, read from
    the already-published `STICKER_STATUS_BLOB` snapshot (never recomputed,
    never a Firestore call). Empty set on any failure or shape mismatch."""
    return _load_sticker_coverage(blob_lkg)


def select_candidatos(reportes: list[dict], state: dict) -> list[dict]:
    """Reportes whose 'id' is NOT already a key in `state['matches']`. Pure,
    no I/O — this is the core incremental invariant: a reporte is excluded
    ONCE it actually has a `state['matches']` entry; an unmatched reporte
    remains a candidate on every future run (a Panel point added later can
    still match it), subject only to `apply_panel_match`'s own per-run cap
    (`PANEL_MATCH_MAX_CANDIDATES_PER_RUN`) on how many of the candidates
    returned here actually get matched in a single call."""
    matches = (state or {}).get("matches") or {}
    out = []
    for r in reportes:
        if not r:
            continue
        rid = r.get("id")
        if rid and rid in matches:
            continue
        out.append(r)
    return out


def _panel_point_to_yx(p: dict) -> dict:
    """`app.jobs.cruce_sticker.load_panel()`'s lowercase
    fuente/registro_id/lat/lon/direccion shape -> the Y/X/DIRECCION/
    registro_id shape `reportes_panel_match.cross_reference` (via
    `cruce_gestor`) expects."""
    return {
        "registro_id": p.get("registro_id"),
        "Y": p.get("lat"),
        "X": p.get("lon"),
        "DIRECCION": p.get("direccion") or "",
    }


def apply_panel_match(reportes: list[dict], *, panel_loader=None, blob_lkg_module=None) -> list[dict]:
    """Orchestration, called from `dashboard_refresh.fetch_reportes()`.
    Never mutates `reportes` or its rows — returns a NEW list, each row a
    NEW dict with an added 'panel' key
    (`{'visitado': bool, 'sticker': bool}`), same "never mutate input"
    discipline as `routers/stickers_atencionsismo.py`'s `redact_for_blob`.

    `panel_loader`/`blob_lkg_module` exist ONLY so tests can inject fakes
    without monkeypatching this module's own imports — default to the real
    `app.jobs.cruce_sticker.load_panel` (imported locally to avoid a
    module-load-time cycle) and the real `blob_lkg` module when omitted.

    A `panel_loader()` or matching-cascade exception is caught here and
    logged — this function must never crash the caller's refresh; on a
    failure it degrades to whatever state existed before this call (no
    partial/half-merged state is ever persisted)."""
    blm = blob_lkg_module if blob_lkg_module is not None else blob_lkg
    state = _load_state(blm)
    candidatos = select_candidatos(reportes, state)
    # Bound the batch (Fix 1a) — see PANEL_MATCH_MAX_CANDIDATES_PER_RUN's own
    # comment: spreads the cost of a large permanently-unmatched backlog
    # across many runs instead of paying for all of it in one. Reportes past
    # the cap simply remain candidates for the NEXT run.
    candidatos = candidatos[:PANEL_MATCH_MAX_CANDIDATES_PER_RUN]

    if candidatos:
        try:
            loader = panel_loader
            if loader is None:
                from app.jobs.cruce_sticker import load_panel
                loader = load_panel
            panel_points = loader() or []
            panel_points_yx = [_panel_point_to_yx(p) for p in panel_points]
            new_matches = reportes_panel_match.cross_reference(candidatos, panel_points_yx)
            if new_matches:
                matches = dict(state.get("matches") or {})
                matches.update(new_matches)
                state = {**state, "matches": matches}
                _save_state(blm, state)
        except Exception:  # noqa: BLE001 - fail-soft, never blocks the refresh
            logging.exception(
                "reportes_panel_state: cruce con Panel falló, sigo con el estado previo"
            )

    sticker_ids = _load_sticker_coverage(blm)
    matches = state.get("matches") or {}
    out: list[dict] = []
    for r in reportes:
        row = dict(r) if isinstance(r, dict) else {}
        match = matches.get(row.get("id"))
        if match is None:
            row["panel"] = {"visitado": False, "sticker": False}
        else:
            row["panel"] = {
                "visitado": True,
                "sticker": match.get("panel_registro_id") in sticker_ids,
            }
        out.append(row)
    return out
