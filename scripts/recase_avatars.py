r"""Re-cast residents whose avatar identity has the wrong sex.

`build_avatar_gallery.py` trusted the source galleries' `face_crops.gender`
column, which is unreliable, so 201 male residents ended up holding a female
identity (43 from the mixed celebrity gallery, 159 from the female-only
galleries). A full rebuild would churn every resident, so this fixes only the
mismatched ones and leaves the correct 380 alone.

Truth sources:
  * `D:\miniville\celebrity_gender.json` — sex verified offline with insightface
    for the mixed `G:\Galleries\Celebrities` gallery (from
    verify_celebrity_gender.py, which no longer calls TMDB).
  * `G:\Gallery` and `F:\amd\gallery` are female-only by construction.

The male pool is `G:\Galleries\Celebrities` filtered to verified males.
It is smaller than the demand today, so this is meant to be re-run as the
IMDb scraper grows the gallery — it is idempotent and resumable.

Run:
    .\.venv\Scripts\python.exe scripts\recase_avatars.py [--dry-run] [--limit N]
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
sys.path.insert(0, str(REPO / "scripts"))
from miniville import db as mvdb            # noqa: E402
import build_avatar_gallery as bag          # noqa: E402

MAPPING = bag.OUT_ROOT / "avatar_mapping.json"
GAL_DIR = bag.OUT_ROOT / "gallery"
AV_DIR = bag.OUT_ROOT / "avatars"


def src_name(gallery_dir: str) -> str:
    """`a0016_jill_wagner_82943` -> `jill_wagner_82943`."""
    return re.sub(r"^a\d{4}_", "", gallery_dir or "")


def identity_sex(gallery_dir: str, verified: dict[str, int]) -> str | None:
    """True sex of the identity behind a cast entry, or None if unknown."""
    g = verified.get(src_name(gallery_dir))
    if g == 2:
        return "Male"
    if g == 1:
        return "Female"
    if g == 0:
        return None                      # could not be resolved offline
    return "Female"                      # the other galleries are female-only


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)

    if not MAPPING.is_file():
        print(f"no mapping at {MAPPING} — run build_avatar_gallery.py first")
        return 1
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    cast = mapping["cast"]
    verified = bag.load_verified()
    if not verified:
        print("no verified sex map — run scripts/verify_celebrity_gender.py first")
        return 1

    # who is wrong, and which male identities are already correctly in use
    fix, used_male = [], set()
    for e in cast:
        isex = identity_sex(e.get("gallery_dir", ""), verified)
        if e.get("sex") == "Male":
            if isex == "Female":
                fix.append(e)
            elif isex == "Male":
                used_male.add(src_name(e["gallery_dir"]))
    if a.limit:
        fix = fix[: a.limit]
    print(f"cast={len(cast)}  male residents holding a female identity={len(fix)}")

    # verified male pool, minus identities already worn by a correct male
    src_db = bag.MALE_SRC / "gallery.db"
    if not src_db.is_file():
        print(f"no source gallery at {src_db}")
        return 1
    conn = sqlite3.connect(src_db)
    conn.row_factory = sqlite3.Row
    pool = bag.pool(conn, 1, verified=verified)
    avail = {iid: d for iid, d in pool.items() if d["name"] not in used_male}
    print(f"verified male identities: {len(pool)}  unused: {len(avail)}")

    mv = mvdb.connect()
    out = sqlite3.connect(bag.OUT_DB)
    out.row_factory = sqlite3.Row
    out.executescript(bag.FACE_DB_SCHEMA)

    used = set()
    fixed, deferred = 0, []
    for e in fix:
        rid = e["resident_id"]
        row = mv.execute("SELECT id, name, age FROM agents WHERE id=?", (rid,)).fetchone()
        if not row:
            continue
        iid = bag.pick(avail, used, row["age"] or 30)
        if iid is None:
            deferred.append(rid)
            continue
        ident = avail[iid]
        used.add(iid)
        dirname = f"a{rid:04d}_{ident['name']}"
        ddir = GAL_DIR / dirname
        portrait = AV_DIR / f"a{rid:04d}.jpg"
        old_dir = GAL_DIR / e.get("gallery_dir", "")
        print(f"  id{rid:>4} {row['name']:<24} {src_name(e['gallery_dir'])} -> {ident['name']}")
        if a.dry_run:
            fixed += 1
            continue

        ddir.mkdir(parents=True, exist_ok=True)
        out.execute(
            "INSERT OR IGNORE INTO identities(name,status,sample_count,notes) "
            "VALUES(?,?,?,?)",
            (dirname, "locked", len(ident["rows"]), f"src=Male:{ident['name']}"))
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
                 crop["landmarks_5pts"], crop.get("landmarks_106"), 1, crop["age"],
                 crop["embedding"], crop["feature_norm"], crop["quality_score"], 1))
            if j == 0 and src_img.exists():
                try:
                    bag.portrait_crop(src_img, json.loads(crop["bbox"]), portrait)
                except Exception as ex:                      # noqa: BLE001
                    print(f"    crop failed: {ex}")
        mv.execute("UPDATE agents SET avatar_path=? WHERE id=?",
                   (f"/avatars/{portrait.name}", rid))

        # retire the old (wrong-sex) identity so it can be reused correctly
        if old_dir.is_dir() and old_dir != ddir:
            shutil.rmtree(old_dir, ignore_errors=True)
            out.execute(
                "DELETE FROM face_crops WHERE identity_id IN "
                "(SELECT id FROM identities WHERE name=?)",
                (e["gallery_dir"],))
            out.execute("DELETE FROM identities WHERE name=?", (e["gallery_dir"],))

        e["identity"] = ident["name"]
        e["gallery_dir"] = dirname
        e["src_age"] = ident["median_age"]
        fixed += 1

    if not a.dry_run:
        out.commit()
        mv.commit()
        MAPPING.write_text(json.dumps(
            {"cast": cast, "deferred_ids": mapping.get("deferred_ids", [])},
            indent=1), encoding="utf-8")

    print(f"\nfixed {fixed}; still deferred (pool exhausted): {len(deferred)}")
    if deferred:
        print("re-run after the scraper grows the gallery: "
              "Import-IMDbStarMeter.ps1 -Phase both, then "
              "verify_celebrity_gender.py, then this script")
    return 0


if __name__ == "__main__":
    sys.exit(main())
