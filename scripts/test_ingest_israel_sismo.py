"""scripts/test_ingest_israel_sismo.py — pytest, offline (no ArcGIS, no
Firestore network calls). Mirrors backend/tests/jobs/test_planeacion_cruce.py's
fake-Firestore convention (bare classes standing in for db/collection/batch).

Deliberately pytest, not the bare-`assert` self-check style the other
`scripts/test_*.py` siblings use (see docs/ADR.md:44, "sin CI test gate:
validación manual + self-checks por archivo") — this module needs to mock
Firestore batching and an ArcGIS fetch across many isolated cases, which a
flat self-check script can't do cleanly. Run manually before treating a
change here as verified: `python -m pytest scripts/test_ingest_israel_sismo.py`
(NOT `python scripts/test_ingest_israel_sismo.py` — that runs zero
assertions, same as any other file with no `if __name__ == "__main__"` block).

Verifies `ingest_israel_sismo.fetch_israel_records()` upserts the FULL
`israel_to_cali.remap()` payload per feature (not just x/y/direccion), keyed
by the feature's raw globalid — plus the batching/--dry/NaN-safety contract
of `main()`. `fetch_raw`, `cali_schema` and `fill_comuna_barrio` are
monkeypatched (network + disk + shapely); `remap` itself is the REAL
israel_to_cali function, so the remap output asserted here is genuine.
"""
from __future__ import annotations

import sys

import pandas as pd
import pytest

import ingest_israel_sismo as ingest

# A small, realistic slice of the Cali schema -- enough to exercise the
# fields israel_to_cali.remap actually derives.
SCHEMA = [
    "direccion", "n_pisos", "criterio_habitabilidad", "criterio_color",
    "colapso_total", "colapso_parcial", "nivel_dano", "danos_estructura",
    "sistema_estructural", "material_estructura", "x", "y",
    "comuna", "barrio_geo", "GlobalID", "ObjectID", "municipio", "fuente",
]


def _fake_fill_comuna_barrio(df):
    """Stand-in for the real point-in-polygon fill (shapely + basemap
    files): not what this suite verifies, so it's replaced with a fixed
    value that still proves fill_comuna_barrio's result reaches the doc."""
    df["comuna"] = "COMUNA 01"
    df["barrio_geo"] = "Barrio Test"
    return df


@pytest.fixture(autouse=True)
def _isolate_from_disk_and_geo(monkeypatch):
    """Every test is isolated from web/data/inspections.json (cali_schema)
    and from basemaps/shapely (fill_comuna_barrio) -- neither is what this
    suite verifies. fetch_raw is set per-test."""
    monkeypatch.setattr(ingest, "cali_schema", lambda: list(SCHEMA))
    monkeypatch.setattr(ingest, "fill_comuna_barrio", _fake_fill_comuna_barrio)


def _row(gid_key="globalid", gid="G-1", **overrides):
    base = {gid_key: gid, "lon": -76.5300, "lat": 3.4200}
    base.update(overrides)
    return base


def _fake_fetch_raw(rows):
    return lambda: pd.DataFrame(rows)


def _rows(n):
    return [_row(gid=f"G-{i}") for i in range(n)]


# ── happy path: the FULL remapped record reaches the doc ────────────────────


def test_happy_path_writes_the_full_remapped_record(monkeypatch):
    row = _row(
        gid="ISR-001",
        BldDetailedRate=2, CollapseElements=2, bldTendency=1,
        buildingType=1, ArchitecturalDmg=1, cracksCritical=1,
        Building_Address="Calle 10 # 5-20", floorNo=3, objectid=55,
    )
    monkeypatch.setattr(ingest, "fetch_raw", _fake_fetch_raw([row]))

    records = ingest.fetch_israel_records()

    assert len(records) == 1
    gid, data = records[0]
    assert gid == "ISR-001"
    assert data["direccion"] == "Calle 10 # 5-20"
    assert data["n_pisos"] == 3
    assert data["criterio_habitabilidad"] == "i2"
    assert data["criterio_color"] == "rojo"
    assert data["colapso_total"] == "si"
    assert data["nivel_dano"] == "alto"
    assert data["danos_estructura"] == "severo"
    assert data["x"] == -76.5300 and data["y"] == 3.4200
    assert data["ObjectID"] == "isr-55"
    assert data["GlobalID"] == "ISR-001"
    assert data["comuna"] == "COMUNA 01" and data["barrio_geo"] == "Barrio Test"


# ── globalid handling ────────────────────────────────────────────────────


def test_row_without_globalid_or_capital_variant_is_dropped(monkeypatch):
    row = {"Building_Address": "Sin id", "lon": -76.5, "lat": 3.4}
    monkeypatch.setattr(ingest, "fetch_raw", _fake_fetch_raw([row]))

    assert ingest.fetch_israel_records() == []


def test_capital_globalid_is_accepted_as_a_fallback(monkeypatch):
    # israel_to_cali.FIELD_MAP only maps the lowercase `globalid` key, so a
    # feature served under the capital fallback would previously reach
    # Firestore keyed correctly but with `GlobalID: None` INSIDE the doc.
    # Assert the doc's own field, not just the returned key, or this
    # regresses silently again.
    row = _row(gid_key="GlobalID", gid="CAP-1")
    monkeypatch.setattr(ingest, "fetch_raw", _fake_fetch_raw([row]))

    records = ingest.fetch_israel_records()

    assert [gid for gid, _ in records] == ["CAP-1"]
    assert records[0][1]["GlobalID"] == "CAP-1"


def test_empty_feature_list_yields_no_records(monkeypatch):
    monkeypatch.setattr(ingest, "fetch_raw", _fake_fetch_raw([]))

    assert ingest.fetch_israel_records() == []


# ── NaN/None safety ──────────────────────────────────────────────────────


def test_missing_and_nan_survey_fields_serialize_as_none_not_nan(monkeypatch):
    row = _row(gid="G-NAN")  # no Building_Address, no BldDetailedRate, ...
    monkeypatch.setattr(ingest, "fetch_raw", _fake_fetch_raw([row]))

    _, data = ingest.fetch_israel_records()[0]

    assert data["direccion"] is None
    assert data["criterio_habitabilidad"] is None
    assert data["colapso_total"] is None
    # No NaN leaks through anywhere in the payload (NaN != NaN is the tell).
    assert not any(isinstance(v, float) and v != v for v in data.values())


def test_missing_coordinates_are_written_as_none_not_excluded(monkeypatch):
    row = _row(gid="G-NOCOORD", lon=None, lat=None)
    monkeypatch.setattr(ingest, "fetch_raw", _fake_fetch_raw([row]))

    records = ingest.fetch_israel_records()

    assert len(records) == 1
    gid, data = records[0]
    assert gid == "G-NOCOORD"
    assert data["x"] is None and data["y"] is None


def test_malformed_feature_with_many_missing_fields_does_not_crash(monkeypatch):
    row = {"globalid": "G-MALFORMED"}  # no lon/lat, no survey fields at all
    monkeypatch.setattr(ingest, "fetch_raw", _fake_fetch_raw([row]))

    records = ingest.fetch_israel_records()

    assert len(records) == 1
    gid, data = records[0]
    assert gid == "G-MALFORMED"
    assert data["direccion"] is None
    assert data["x"] is None and data["y"] is None


def test_reindexes_against_the_real_cali_schema(monkeypatch):
    """Regression guard: the other tests reindex against the trimmed SCHEMA
    fixture (18 cols), so none of them would catch a real schema drift
    (e.g. inspections.json losing/renaming a column). This one overrides
    the autouse fixture's fake cali_schema with the REAL function, which
    reads web/data/inspections.json for real."""
    from israel_to_cali import cali_schema as real_cali_schema
    monkeypatch.setattr(ingest, "cali_schema", real_cali_schema)
    row = _row(gid="REAL-1", BldDetailedRate=1, CollapseElements=1)
    monkeypatch.setattr(ingest, "fetch_raw", _fake_fetch_raw([row]))

    records = ingest.fetch_israel_records()

    assert len(records) == 1
    _, data = records[0]
    assert set(data.keys()) >= set(real_cali_schema())


# ── Firestore batching (main()) ─────────────────────────────────────────


class _FakeDocRef:
    def __init__(self, doc_id):
        self.id = doc_id


class _FakeCollection:
    def document(self, doc_id):
        return _FakeDocRef(doc_id)


class _FakeBatch:
    def __init__(self, commits):
        self._commits = commits
        self._pending = []

    def set(self, doc_ref, data, merge=False):
        self._pending.append((doc_ref.id, data, merge))

    def commit(self):
        self._commits.append(list(self._pending))
        self._pending = []


class _FakeDb:
    def __init__(self):
        self.commits: list[list[tuple[str, dict, bool]]] = []

    def collection(self, name):
        return _FakeCollection()

    def batch(self):
        return _FakeBatch(self.commits)


def test_exactly_400_features_commits_at_the_cap_and_again_at_the_end(monkeypatch):
    monkeypatch.setattr(ingest, "fetch_raw", _fake_fetch_raw(_rows(400)))
    db = _FakeDb()
    monkeypatch.setattr(ingest, "_client", lambda: db)
    monkeypatch.setattr(sys, "argv", ["ingest_israel_sismo.py"])

    ingest.main()

    assert len(db.commits) == 2
    assert len(db.commits[0]) == 400
    # Known, accepted quirk (not the point being tested): main() always
    # commits once more after the loop, so an exact multiple of 400 fires
    # one empty, no-op commit. Asserted here so a future fix to that is a
    # deliberate change, not an accidental regression of this test.
    assert len(db.commits[1]) == 0
    assert sum(len(c) for c in db.commits) == 400
    all_ids = [doc_id for commit in db.commits for doc_id, _, _ in commit]
    assert all_ids[0] == "G-0" and all_ids[-1] == "G-399"
    assert all(merge is True for commit in db.commits for _, _, merge in commit)


def test_401_features_triggers_two_non_empty_commits(monkeypatch):
    monkeypatch.setattr(ingest, "fetch_raw", _fake_fetch_raw(_rows(401)))
    db = _FakeDb()
    monkeypatch.setattr(ingest, "_client", lambda: db)
    monkeypatch.setattr(sys, "argv", ["ingest_israel_sismo.py"])

    ingest.main()

    assert len(db.commits) == 2
    assert len(db.commits[0]) == 400
    assert len(db.commits[1]) == 1
    assert db.commits[0][0][0] == "G-0" and db.commits[0][-1][0] == "G-399"
    assert db.commits[1][0][0] == "G-400"
    assert all(merge is True for commit in db.commits for _, _, merge in commit)


def test_dry_run_fetches_and_counts_but_never_writes(monkeypatch, capsys):
    monkeypatch.setattr(ingest, "fetch_raw", _fake_fetch_raw(_rows(3)))

    def _forbidden_client():
        raise AssertionError("--dry must never touch Firestore")
    monkeypatch.setattr(ingest, "_client", _forbidden_client)
    monkeypatch.setattr(sys, "argv", ["ingest_israel_sismo.py", "--dry"])

    ingest.main()  # must not raise

    out = capsys.readouterr().out
    assert "3 puntos" in out
    assert "sin escribir" in out
