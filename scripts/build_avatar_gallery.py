r"""Build the Miniville resident gallery from ColONEL-KFC galleries.

Female residents  <- G:\Gallery        (adult gallery, ~930 identities w/ exemplars)
Male residents    <- G:\Galleries\Celebrities (~90 male identities; reuse allowed)

Per resident we:
  1. pick an identity whose exemplar crops' median age is closest (same gender),
  2. copy up to SAMPLES exemplar source images to D:\miniville\gallery\<dir>\,
  3. copy their antelopev2 face_crops rows into D:\miniville\gallery\gallery.db
     (same vectors vw reindex-gallery would produce - zero GPU cost),
  4. crop a face portrait (bbox + margins) to D:\miniville\avatars\a<id>.jpg
  5. update agents.avatar_path -> /avatars/a<id>.jpg

Run:  .\.venv\Scripts\python.exe scripts\build_avatar_gallery.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from miniville import db as mvdb  # noqa: E402

FEMALE_SRC = Path(r"G:\Gallery")
MALE_SRC = Path(r"G:\Galleries\Celebrities")
OUT_ROOT = Path(r"D:\miniville")
SAMPLES = 4          # exemplar source images per resident (>= vw reindex minimum)
OUT_DB = OUT_ROOT / "gallery" / "gallery.db"
# TMDB-verified sex for the MIXED celebrity gallery, from
# scripts/verify_celebrity_gender.py. The gallery's own face_crops.gender column
# is unreliable (it mislabels angled/profile crops), so when this file exists we
# trust it over the column. TMDB gender: 1 = female, 2 = male.
VERIFIED_SEX = OUT_ROOT / "celebrity_gender.json"

FACE_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE, status TEXT DEFAULT 'soft', threshold REAL DEFAULT 0.45,
    sample_count INTEGER, created_at TEXT, validated_at TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS face_crops (
    id INTEGER PRIMARY KEY AUTOINCREMENT, identity_id INTEGER, model_name TEXT,
    image_path TEXT, rel_path TEXT, bbox TEXT, landmarks_5pts TEXT,
    landmarks_106 TEXT, gender INTEGER, age REAL, embedding BLOB,
    feature_norm REAL, quality_score REAL, is_exemplar INTEGER, created_at TEXT);
"""


def load_verified(path: Path = VERIFIED_SEX) -> dict[str, int]:
    """gallery dir name -> TMDB gender (1 female / 2 male). Empty if absent."""
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: (v or {}).get("gender", 0) for k, v in raw.items()}


def pool(conn: sqlite3.Connection, gender: int,
         verified: dict[str, int] | None = None) -> dict[int, dict]:
    """identity_id -> {name, median_age, crops:[rows]} from a source gallery.

    `gender` uses the insightface convention (0 female / 1 male). When
    `verified` is supplied it decides membership instead of the per-crop
    `f.gender` column, which is what let female faces into the male pool.
    """
    crops = conn.execute(
        """SELECT f.identity_id, i.name, f.age, f.bbox, f.image_path, f.rel_path,
                  f.landmarks_5pts, f.landmarks_106, f.embedding, f.feature_norm,
                  f.quality_score, f.is_exemplar, f.gender
           FROM face_crops f JOIN identities i ON i.id = f.identity_id
           WHERE f.is_exemplar = 1 AND i.status != 'invalid'
           ORDER BY f.quality_score DESC""").fetchall()
    want = 1 if gender == 0 else 2          # insightface 0/1 -> TMDB 1/2
    by_id: dict[int, dict] = {}
    for r in crops:
        if verified is not None:
            if verified.get(r["name"], 0) != want:
                continue
        elif r["gender"] != gender:
            continue
        d = by_id.setdefault(r["identity_id"], {"name": r["name"], "ages": [], "rows": []})
        if r["age"]:
            d["ages"].append(r["age"])
        d["rows"].append(dict(r))
    for d in by_id.values():
        d["ages"].sort()
        d["median_age"] = d["ages"][len(d["ages"]) // 2] if d["ages"] else 30.0
        d["rows"] = d["rows"][:SAMPLES]
    return by_id


def pick(identities: dict[int, dict], used: set[int], age: float) -> int | None:
    """Nearest-age UNUSED identity. Returns None when the pool is exhausted —
    galleries keep growing (IMDb/TMDb scraper), so we never reuse a face."""
    best, best_d = None, 1e9
    for iid, d in identities.items():
        if iid in used:
            continue
        delta = abs(d["median_age"] - age)
        if delta < best_d:
            best, best_d = iid, delta
    return best


def portrait_crop(src: Path, bbox: list[float], dst: Path) -> None:
    from PIL import Image
    im = Image.open(src).convert("RGB")
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    # face box + margin: hair above, chin/neck below, shoulders at sides
    x1 = max(0, x1 - w * 0.45); x2 = min(im.width, x2 + w * 0.45)
    y1 = max(0, y1 - h * 0.55); y2 = min(im.height, y2 + h * 1.05)
    im.crop((int(x1), int(y1), int(x2), int(y2))).resize(
        (256, 256), Image.LANCZOS).save(dst, "JPEG", quality=88)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    mv = mvdb.connect()
    residents = mv.execute(
        "SELECT id, name, sex, age FROM agents WHERE alive=1 ORDER BY id").fetchall()
    if a.limit:
        residents = residents[: a.limit]

    srcs = {"Female": FEMALE_SRC, "Male": MALE_SRC}
    conns = {s: sqlite3.connect(p / "gallery.db") for s, p in srcs.items() if p.exists()}
    for c in conns.values():
        c.row_factory = sqlite3.Row
    verified = load_verified()
    if verified:
        print(f"verified sex map: {len(verified)} identities from {VERIFIED_SEX}")
    else:
        print("WARNING: no verified sex map — falling back to the unreliable "
              "face_crops.gender column. Run scripts/verify_celebrity_gender.py")
    pools = {s: pool(conns[s], 0 if s == "Female" else 1,
                     verified=verified if s == "Male" else None) for s in conns}
    # insightface gender: 0=female, 1=male; the male pool is TMDB-verified
    used: dict[str, set[int]] = {s: set() for s in pools}
    gal_dir = OUT_ROOT / "gallery"
    av_dir = OUT_ROOT / "avatars"
    gal_dir.mkdir(parents=True, exist_ok=True)
    av_dir.mkdir(parents=True, exist_ok=True)

    out = sqlite3.connect(OUT_DB)
    out.row_factory = sqlite3.Row
    out.executescript(FACE_DB_SCHEMA)

    mapping, deferred, done = [], [], 0
    for r in residents:
        sex = r["sex"] if r["sex"] in pools else "Female"
        iid = pick(pools[sex], used[sex], r["age"] or 30)
        if iid is None:
            deferred.append(r["id"])
            continue
        ident = pools[sex][iid]
        used[sex].add(iid)
        dirname = f"a{r['id']:04d}_{ident['name']}"
        ddir = gal_dir / dirname
        portrait = av_dir / f"a{r['id']:04d}.jpg"
        if not a.dry_run:
            ddir.mkdir(exist_ok=True)
            out.execute(
                "INSERT OR IGNORE INTO identities(name,status,sample_count,notes) "
                "VALUES(?,?,?,?)",
                (dirname, "locked", len(ident["rows"]),
                 f"src={sex}:{ident['name']}"))
            new_iid = out.execute(
                "SELECT id FROM identities WHERE name=?", (dirname,)).fetchone()["id"]
            for j, crop in enumerate(ident["rows"]):
                src_img = Path(crop["image_path"])
                dst_img = ddir / src_img.name
                if src_img.exists() and not dst_img.exists():
                    shutil.copy2(src_img, dst_img)
                out.execute(
                    """INSERT INTO face_crops(identity_id,model_name,image_path,rel_path,
                       bbox,landmarks_5pts,landmarks_106,gender,age,embedding,
                       feature_norm,quality_score,is_exemplar)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (new_iid, dirname, str(dst_img), src_img.name, crop["bbox"],
                     crop["landmarks_5pts"], crop.get("landmarks_106"),
                     0 if sex == "Female" else 1, crop["age"], crop["embedding"],
                     crop["feature_norm"], crop["quality_score"], 1))
                if j == 0 and src_img.exists():
                    try:
                        portrait_crop(src_img, json.loads(crop["bbox"]), portrait)
                    except Exception as e:
                        print(f"  crop failed {dirname}: {e}")
            mv.execute("UPDATE agents SET avatar_path=? WHERE id=?",
                       (f"/avatars/{portrait.name}", r["id"]))
        mapping.append({"resident_id": r["id"], "name": r["name"], "sex": sex,
                        "identity": ident["name"], "gallery_dir": dirname,
                        "src_age": ident["median_age"]})
        done += 1
        if done % 100 == 0:
            print(f"{done}/{len(residents)}")
    if not a.dry_run:
        out.commit(); mv.commit()
    (OUT_ROOT / "avatar_mapping.json").write_text(
        json.dumps({"cast": mapping, "deferred_ids": deferred}, indent=1),
        encoding="utf-8")
    uniq = len({m["identity"] for m in mapping})
    print(f"done: {done} cast ({uniq} identities), {len(deferred)} deferred "
          f"(grow the pool: Import-IMDbStarMeter.ps1 -Phase both)")
    print(f"dry-run={a.dry_run}  gallery={gal_dir}  avatars={av_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
