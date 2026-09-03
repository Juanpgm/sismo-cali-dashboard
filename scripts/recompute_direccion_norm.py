"""One-off, offline migration: recompute `direccion_norm` in-place across
`web/data/inspections.json` using the fixed `normalize_direccion()` (see the
address_norm.py / refresh_data.py boundary-fix for glued abbreviations like
"Carrera77" and the new lone-"K" carrera alias).

Deliberately does NOT run scripts/refresh_data.py: that pipeline re-pulls
from Firestore/ArcGIS, and the project just came out of a Firestore 429-quota
incident (see git log). This script only reads the already-published JSON
file, recomputes ONE field per record, and writes the file back -- no
network calls, no other field touched.

Usage:
    python scripts/recompute_direccion_norm.py [--dry-run]

`--dry-run` prints the same summary without writing the file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refresh_data import normalize_direccion  # noqa: E402

INSPECTIONS_PATH = Path(__file__).resolve().parents[1] / "web" / "data" / "inspections.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Recompute and report, but do not write the file.")
    args = parser.parse_args()

    raw_text = INSPECTIONS_PATH.read_text(encoding="utf-8")
    records = json.loads(raw_text)
    if not isinstance(records, list):
        print(f"ERROR: expected a JSON array at {INSPECTIONS_PATH}, got {type(records).__name__}", file=sys.stderr)
        return 1

    original_count = len(records)
    changes: list[tuple[str, str, str]] = []  # (id_edan/ObjectID, before, after)

    for record in records:
        before = record.get("direccion_norm")
        after = normalize_direccion(record.get("direccion"))
        if after != before:
            label = record.get("id_edan") or record.get("ObjectID") or "?"
            changes.append((str(label), "" if before is None else str(before), after))
        record["direccion_norm"] = after

    print(f"Records read: {original_count}")
    print(f"direccion_norm changed: {len(changes)} of {original_count}")
    print()
    for label, before, after in changes[:10]:
        print(f"  [{label}] {before!r} -> {after!r}")
    if len(changes) > 10:
        print(f"  ... and {len(changes) - 10} more")

    if args.dry_run:
        print("\n--dry-run: not writing the file.")
        return 0

    if len(records) != original_count:
        print("ERROR: record count changed during processing -- refusing to write.", file=sys.stderr)
        return 1

    new_text = json.dumps(records, ensure_ascii=False, indent=2)
    INSPECTIONS_PATH.write_text(new_text, encoding="utf-8")

    # Verify the file we just wrote is still valid JSON with the same record count.
    reparsed = json.loads(INSPECTIONS_PATH.read_text(encoding="utf-8"))
    assert isinstance(reparsed, list) and len(reparsed) == original_count, (
        "post-write verification failed: record count or shape changed"
    )
    print(f"\nWrote {INSPECTIONS_PATH} -- verified valid JSON, {len(reparsed)} records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
