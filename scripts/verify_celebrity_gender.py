r"""Verify the sex of every identity in the mixed celebrity gallery, locally.

`G:\Galleries\Celebrities` is the only *mixed* source gallery (the other two,
`G:\Gallery` and `F:\amd\gallery`, are female-only). The gallery's own
`face_crops.gender` column is unreliable on its own — it mislabels angled and
profile crops, which is how female faces got cast onto male residents — but a
*majority vote* across every crop of an identity is not.

This used to resolve each identity against TMDB. TMDB is retired (operator,
Thu, 01 Oct 2026), so the sex is now decided offline with insightface's
`genderage` model over the identity's own face crops. Nothing here touches the
network.

The output format is unchanged — `{gallery_dir: {"gender": 1|2|0}}` with the
TMDB convention (1 female, 2 male, 0 unknown) — so `build_avatar_gallery.py`
and `recase_avatars.py` keep working. Each entry also stores `n_crops`: a
re-embed that changes an identity's crop set invalidates its cached label and
the identity is re-voted on the next run.

Run (needs insightface; use the ColONEL-KFC venv):
    .\.venv\Scripts\python.exe scripts\verify_celebrity_gender.py [--force]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

GALLERY = Path(r"G:\Galleries\Celebrities")
CACHE = Path(r"D:\miniville\celebrity_gender.json")

# insightface genderage reports 'M'/'F'; the cache uses the TMDB convention
SEX_TO_TMDB = {"M": 2, "F": 1}
# how many crops must agree before the vote is trusted at all
MIN_VOTES = 2
# ...and the winner must beat the loser by better than 2:1. A plain majority is
# not good enough: sampling real identities found a woman (Nicole Kidman) voted
# male 4-2, and a false *male* is exactly the error that cast female faces onto
# male residents in the first place. Ambiguous identities stay unresolved and
# are simply not cast.
VOTE_MARGIN = 2


def identity_crops(conn: sqlite3.Connection, dirname: str) -> list[str]:
    rows = conn.execute(
        """SELECT f.image_path FROM face_crops f
           JOIN identities i ON i.id = f.identity_id
           WHERE i.name = ? AND f.image_path IS NOT NULL""", (dirname,)).fetchall()
    return [r["image_path"] for r in rows]


def vote_sex(app, paths: list[str], max_crops: int = 0) -> tuple[int, int, int]:
    """Return (tmdb_gender, male_votes, female_votes) for one identity."""
    import cv2

    sample = paths if max_crops <= 0 else paths[:max_crops]
    male = female = 0
    for path in sample:
        img = cv2.imread(path)
        if img is None:
            continue
        faces = app.get(img)
        if not faces:
            continue
        # the biggest face in the frame is the subject
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        raw = getattr(face, "sex", None)
        if raw == "M":
            male += 1
        elif raw == "F":
            female += 1
    if male + female < MIN_VOTES:
        return 0, male, female
    if male > VOTE_MARGIN * female:
        return 2, male, female
    if female > VOTE_MARGIN * male:
        return 1, male, female
    return 0, male, female


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gallery", default=str(GALLERY))
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--max-crops", type=int, default=0,
                    help="crops to sample per identity; 0 = all of them. "
                         "Sampling only a few is what left identities undecided: "
                         "with 14 crops one voted M3/F11 over all of them but "
                         "M2/F4 over the first six.")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true",
                    help="re-verify identities that are already resolved")
    args = ap.parse_args(argv)

    import insightface
    from insightface.app import FaceAnalysis

    gallery = Path(args.gallery)
    db = gallery / "gallery.db"
    if not db.is_file():
        print(f"no gallery database at {db}")
        return 1

    cache_path = Path(args.cache)
    cache: dict[str, dict] = {}
    if cache_path.is_file():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    dirs = sorted(p.name for p in gallery.iterdir()
                  if p.is_dir() and not p.name.startswith("."))
    crop_counts = dict(conn.execute(
        """SELECT i.name, COUNT(*) FROM face_crops f
           JOIN identities i ON i.id = f.identity_id
           WHERE f.image_path IS NOT NULL GROUP BY i.name""").fetchall())

    todo = []
    for d in dirs:
        entry = cache.get(d) or {}
        resolved = entry.get("gender") in (1, 2)
        fresh = entry.get("n_crops") == crop_counts.get(d, 0)
        if args.force or not (resolved and fresh):
            todo.append(d)
    if args.limit:
        todo = todo[: args.limit]

    print(f"gallery dirs: {len(dirs)}   cached: {len(cache)}   to verify: {len(todo)}")
    if not todo:
        print("nothing to do")
        return 0

    app = FaceAnalysis(name="antelopev2", allowed_modules=["detection", "genderage"])
    app.prepare(ctx_id=0, det_size=(640, 640))

    changed = 0
    for i, d in enumerate(todo, 1):
        paths = identity_crops(conn, d)
        if not paths:
            cache[d] = {"name": None, "gender": 0, "source": "local",
                        "n_crops": 0, "error": "no face crops"}
            continue
        gender, male, female = vote_sex(app, paths, args.max_crops)
        cache[d] = {"name": None, "gender": gender, "source": "local",
                    "n_crops": len(paths),
                    "votes": {"male": male, "female": female}}
        if gender:
            changed += 1
        if i % 25 == 0:
            cache_path.write_text(json.dumps(cache, indent=1), encoding="utf-8")
            print(f"  {i}/{len(todo)} …")

    cache_path.write_text(json.dumps(cache, indent=1), encoding="utf-8")

    male = [d for d, v in cache.items() if v.get("gender") == 2]
    female = [d for d, v in cache.items() if v.get("gender") == 1]
    unknown = [d for d, v in cache.items() if v.get("gender") not in (1, 2)]
    print(f"\nverified: {len(cache)}   male={len(male)}   female={len(female)}   "
          f"unknown={len(unknown)}   (resolved this pass: {changed})")
    print(f"cache -> {cache_path}")
    if unknown:
        print("unknown sample:", ", ".join(sorted(unknown)[:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
