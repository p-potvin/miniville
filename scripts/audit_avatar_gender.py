r"""Audit resident avatars against the sex recorded on the agent row.

The source galleries carry a `face_crops.gender` column, but it is unreliable
(it mislabels angled/profile crops), and both the gallery builder and the
ColONEL-KFC bridge trusted it. That let female faces be cast onto male
residents. This script re-checks the *rendered* 256px portraits — clean frontal
crops — with insightface's genderage model, which is far more accurate on them.

Run (needs insightface; use the ColONEL-KFC venv):
    .\.venv\Scripts\python.exe scripts\audit_avatar_gender.py [--json out.json]

Exit code is 0 when every avatar matches, 1 when mismatches are found.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

AVATARS = Path(r"D:\miniville\avatars")
DB = Path(__file__).resolve().parents[1] / "data" / "miniville.db"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--avatars", default=str(AVATARS))
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--json", default=None, help="write the full report here")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    import cv2
    import insightface
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="antelopev2", allowed_modules=["detection", "genderage"])
    app.prepare(ctx_id=0, det_size=(640, 640))

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, sex, avatar_path FROM agents "
        "WHERE avatar_path IS NOT NULL AND alive=1 ORDER BY id").fetchall()
    if args.limit:
        rows = rows[: args.limit]

    av_root = Path(args.avatars)
    report, mismatches, unreadable = [], [], []
    for r in rows:
        p = av_root / Path(r["avatar_path"]).name
        if not p.is_file():
            unreadable.append({"id": r["id"], "name": r["name"], "path": str(p)})
            continue
        img = cv2.imread(str(p))
        if img is None:
            unreadable.append({"id": r["id"], "name": r["name"], "path": str(p)})
            continue
        faces = app.get(img)
        if not faces:
            unreadable.append({"id": r["id"], "name": r["name"], "path": str(p)})
            continue
        f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
        # genderage reports 'M'/'F'; normalise to the agents.sex vocabulary
        raw = getattr(f, "sex", None)
        face_sex = {"M": "Male", "F": "Female"}.get(raw, raw)
        rec = {"id": r["id"], "name": r["name"], "sex": r["sex"],
               "face_sex": face_sex, "avatar": p.name}
        report.append(rec)
        if face_sex and face_sex != r["sex"]:
            mismatches.append(rec)

    print(f"checked {len(report)} avatars; {len(mismatches)} mismatched; "
          f"{len(unreadable)} unreadable")
    by_pair: dict[str, int] = {}
    for m in mismatches:
        by_pair[f"{m['sex']}->{m['face_sex']}"] = by_pair.get(f"{m['sex']}->{m['face_sex']}", 0) + 1
    for k, v in sorted(by_pair.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    for m in mismatches[:20]:
        print(f"  id{m['id']:>4} {m['name']:<24} agent={m['sex']:<6} "
              f"face={m['face_sex']:<6} {m['avatar']}")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"report": report, "mismatches": mismatches,
             "unreadable": unreadable}, indent=2), encoding="utf-8")
        print(f"report -> {args.json}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
