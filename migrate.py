#!/usr/bin/env python3
"""One-shot: fold pre-project captures into the archive, then tidy the repo.

The four Aug-25 heatmap dumps are live-book snapshots of the ONE stream that
cannot be re-fetched. They must land in the archive before anything is deleted.

Decrypted funding/basis/liq dumps are replayable, so they are moved to data/raw/
for reference rather than imported. The four still-encrypted files are dead --
their per-response AES key was in a header that was never captured -- so they
are moved aside too, not silently destroyed.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from decode import store
from decode.streams import STREAMS

ROOT = Path(__file__).parent
RAW_DIR = ROOT / "data" / "raw"

# (file, stream) -- heatmap captures, oldest first so run order matches real time
HEATMAP_CAPTURES = [
    "heatmap.json",
    "heatmap_25_08_2026_20_11.json",
    "heatmap_20260825_2013.json",
    "heatmap_20260825_2014.json",
    "liq5m.json",
]
ENCRYPTED_DEAD = [
    "en.json", "25_08_2026_20_11.json",
    "funding_btc_last_26_08_26.json", "liquidation_heatmap_last_26_08_26.json",
]
REPLAYABLE_DUMPS = ["funding.json", "basis.json", "basis_hist.json"]
OLD_MODULES = ["cg.py", "cgtoken.py", "cgliq.py", "pressure.py", "collect.py"]


def import_heatmaps(conn) -> int:
    """Import each capture as its own run, deduped by the bar it describes."""
    seen = {r["bar_ts"] for r in store.series(conn, "heatmap") if r.get("bar_ts")}
    n = 0
    for name in HEATMAP_CAPTURES:
        path = ROOT / name
        if not path.exists():
            continue
        payload = json.loads(path.read_text())
        if "liq" not in payload:
            print(f"  skip {name}: not a heatmap payload")
            continue
        row = STREAMS["heatmap"].reduce(payload)
        if row["bar_ts"] in seen:
            print(f"  skip {name}: bar {row['bar_ts']} already archived")
            continue
        rid = store.start_run(conn, note=f"migrated from {name}")
        with store.transaction(conn):
            store.save_processed(conn, rid, "heatmap", row)
        store.finish_run(conn, rid, 1, 0)
        seen.add(row["bar_ts"])
        n += 1
        print(f"  imported {name}: spot ${row['spot']:,.0f}, {len(row['levels'])} levels")
    return n


def import_legacy_jsonl(conn) -> int:
    """Fold the old flat-file collector output in, if present."""
    path = ROOT / "data" / "collector.jsonl"
    if not path.exists():
        return 0
    seen = {r["bar_ts"] for r in store.series(conn, "heatmap") if r.get("bar_ts")}
    n = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        old = json.loads(line)
        rid = store.start_run(conn, note="migrated from collector.jsonl")
        wrote = 0
        for stream in ("funding", "basis", "longshort", "liqtoday", "heatmap"):
            data = old.get(stream)
            if not data:
                continue
            if stream == "heatmap":
                if data.get("bar_ts") in seen:
                    continue
                seen.add(data.get("bar_ts"))
                data = {**data, "levels": [tuple(x) for x in data["levels"]]}
            with store.transaction(conn):
                store.save_processed(conn, rid, stream, data)
            wrote += 1
        store.finish_run(conn, rid, wrote, 0)
        n += wrote
    print(f"  imported {n} rows from collector.jsonl")
    return n


def tidy(apply: bool) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    moves = [(f, "capture (imported)") for f in HEATMAP_CAPTURES]
    moves += [(f, "replayable dump") for f in REPLAYABLE_DUMPS]
    moves += [(f, "ENCRYPTED - key gone, undecodable") for f in ENCRYPTED_DEAD]
    moves += [(f.name, "generated heatmap dump") for f in ROOT.glob("heatmap_2026*.json")]

    seen: set[str] = set()
    for name, why in moves:
        src = ROOT / name
        if not src.exists() or name in seen:
            continue
        seen.add(name)
        print(f"  {'move' if apply else 'would move'} {name:40} -> data/raw/  ({why})")
        if apply:
            shutil.move(str(src), str(RAW_DIR / name))

    legacy = ROOT / "legacy"
    legacy.mkdir(exist_ok=True)
    for name in OLD_MODULES:                 # code, not data -- keep it out of data/
        src = ROOT / name
        if not src.exists():
            continue
        print(f"  {'move' if apply else 'would move'} {name:40} -> legacy/    (superseded)")
        if apply:
            shutil.move(str(src), str(legacy / name))


def main() -> int:
    apply = "--apply" in sys.argv
    conn = store.connect()
    print("importing irreplaceable heatmap captures:")
    n = import_heatmaps(conn) + import_legacy_jsonl(conn)
    print(f"\n{n} rows imported")
    print("\ntidying repo root:" if apply else "\ndry run (pass --apply to move files):")
    tidy(apply)
    s = store.stats(conn)
    print(f"\narchive now: {s['runs']} runs, {s['processed_rows']} processed rows, "
          f"streams={', '.join(s['streams'])}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
