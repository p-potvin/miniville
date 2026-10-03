r"""Purge adult-gallery casts from the Miniville resident gallery.

`build_avatar_gallery.py` originally drew the female pool from `G:\Gallery`,
which turned out to be an adult gallery — so `src=Female:*` identities and the
`src=Male:*` ones whose source name exists in that gallery are all wrong-cast.
This deletes them from `D:\miniville\gallery\gallery.db`, removes their sample
dirs and portraits, and nulls `agents.avatar_path` so the residents queue for a
proper recast. Real celebrity casts (`src=Male:<name>_nm*`) are kept.

Run:
    .\.venv\Scripts\python.exe scripts\purge_bad_casts.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from miniville import db as mvdb            # noqa: E402

OUT_ROOT = Path(r"D:\miniville")
OUT_DB = OUT_ROOT / "gallery" / "gallery.db"
GAL_DIR = OUT_ROOT / "gallery"
AV_DIR = OUT_ROOT / "avatars"
MAPPING = OUT_ROOT / "avatar_mapping.json"
ADULT_DB = Path(r"G:\Gallery\gallery.db")


def adult_names() -> set[str]:
    conn = sqlite3.connect(ADULT_DB)
    names = {r[0] for r in conn.execute("SELECT name FROM identities")}
    conn.close()
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not OUT_DB.is_file():
        print(f"no pool db at {OUT_DB}")
        return 1
    adult = adult_names()
    print(f"adult-gallery identities on file: {len(adult)}")

    out = sqlite3.connect(OUT_DB)
    out.row_factory = sqlite3.Row
    rows = out.execute("SELECT id, name, notes FROM identities").fetchall()

    bad, keep = [], []
    for r in rows:
        notes = r["notes"] or ""
        # new casts carry an explicit source tag and are never purged
        if notes.startswith("src=celebrity:"):
            keep.append(r)
            continue
        # legacy rows: src=Female could only come from the adult gallery;
        # src=Male names need the adult-DB membership check
        src = notes.split(":", 1)[1] if ":" in notes else ""
        is_bad = (notes.startswith("src=Female:")
                  or (notes.startswith("src=Male:") and src in adult))
        (bad if is_bad else keep).append(r)
    print(f"identities: {len(rows)} total, {len(bad)} to purge, {len(keep)} kept")

    rid_re = re.compile(r"^a(\d{4})_")
    purged_rids = set()
    for r in bad:
        m = rid_re.match(r["name"])
        if m:
            purged_rids.add(int(m.group(1)))

    if a.dry_run:
        for r in bad[:10]:
            print("  would purge", r["name"])
        print(f"residents losing their cast: {len(purged_rids)}")
        return 0

    ids = [r["id"] for r in bad]
    names = [r["name"] for r in bad]
    for i in range(0, len(ids), 400):
        out.execute(
            f"DELETE FROM face_crops WHERE identity_id IN "
            f"({','.join('?' * len(ids[i:i + 400]))})",
            ids[i:i + 400])
        out.execute(
            f"DELETE FROM identities WHERE id IN "
            f"({','.join('?' * len(ids[i:i + 400]))})",
            ids[i:i + 400])
    out.commit()
    print(f"deleted {len(bad)} identities + crops from {OUT_DB.name}")

    # files: sample dirs + portraits, then unwire avatar_path
    ndirs = nports = 0
    for name in names:
        d = GAL_DIR / name
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            ndirs += 1
    mv = mvdb.connect()
    for rid in purged_rids:
        p = AV_DIR / f"a{rid:04d}.jpg"
        if p.exists():
            p.unlink()
            nports += 1
        mv.execute("UPDATE agents SET avatar_path=NULL WHERE id=?", (rid,))
    mv.commit()
    print(f"removed {ndirs} sample dirs, {nports} portraits; "
          f"nulled avatar_path for {len(purged_rids)} residents")

    if MAPPING.is_file():
        mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
        before = len(mapping.get("cast", []))
        mapping["cast"] = [e for e in mapping.get("cast", [])
                           if e.get("resident_id") not in purged_rids]
        MAPPING.write_text(json.dumps(mapping, indent=1), encoding="utf-8")
        print(f"mapping: {before} -> {len(mapping['cast'])} cast entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
