r"""Verify the sex of every identity in the mixed celebrity gallery via TMDB.

`G:\Galleries\Celebrities` is the only *mixed* source gallery (the other two,
`G:\Gallery` and `F:\amd\gallery`, are female-only). The gallery's own
`face_crops.gender` column is unreliable, so this resolves each identity
against TMDB instead — one query per identity, cached and resumable.

Identity directory names come in two shapes:
    <name>_nmXXXXXXX   -> IMDb id  -> /find/{nm}?external_source=imdb_id
    <name>_<number>    -> TMDB person id -> /person/{id}

TMDB `gender`: 1 = female, 2 = male, 0 = unspecified.

Run (needs `requests`; the ColONEL-KFC venv has it):
    .\.venv\Scripts\python.exe scripts\verify_celebrity_gender.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

GALLERY = Path(r"G:\Galleries\Celebrities")
CACHE = Path(r"D:\miniville\celebrity_gender.json")
CREDENTIAL = Path(r"C:\Users\Administrator\Desktop\Github Repos\.access\tmdb_api.txt")

IMDB_RE = re.compile(r"_(nm\d{7,})$")
TMDB_RE = re.compile(r"_(\d{2,})$")


def parse_identity(dirname: str) -> tuple[str, str] | None:
    """Return ('imdb', id) or ('tmdb', id) for a gallery directory name."""
    m = IMDB_RE.search(dirname)
    if m:
        return "imdb", m.group(1)
    m = TMDB_RE.search(dirname)
    if m:
        return "tmdb", m.group(1)
    return None


def lookup(session, key: str, kind: str, ident: str) -> dict:
    if kind == "imdb":
        url = f"https://api.themoviedb.org/3/find/{ident}"
        params = {"api_key": key, "external_source": "imdb_id"}
    else:
        url = f"https://api.themoviedb.org/3/person/{ident}"
        params = {"api_key": key}
    r = session.get(url, params=params, timeout=20)
    if r.status_code == 429:
        time.sleep(2.0)
        r = session.get(url, params=params, timeout=20)
    r.raise_for_status()
    j = r.json()
    if kind == "imdb":
        p = (j.get("person_results") or [{}])[0]
    else:
        p = j
    return {"name": p.get("name"), "gender": p.get("gender", 0),
            "tmdb_id": p.get("id")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gallery", default=str(GALLERY))
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--credential", default=str(CREDENTIAL))
    ap.add_argument("--rate", type=float, default=20.0, help="requests/sec ceiling")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    import requests

    key = Path(args.credential).read_text(encoding="utf-8").strip()
    cache_path = Path(args.cache)
    cache: dict[str, dict] = {}
    if cache_path.is_file():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    dirs = sorted(p.name for p in Path(args.gallery).iterdir() if p.is_dir())
    todo = [d for d in dirs if d not in cache]
    if args.limit:
        todo = todo[: args.limit]
    print(f"gallery dirs: {len(dirs)}   cached: {len(cache)}   to look up: {len(todo)}")

    session = requests.Session()
    delay = 1.0 / args.rate
    done = 0
    for d in todo:
        parsed = parse_identity(d)
        if not parsed:
            cache[d] = {"name": None, "gender": 0, "error": "unparseable dir name"}
            continue
        kind, ident = parsed
        try:
            cache[d] = lookup(session, key, kind, ident)
        except Exception as e:                      # noqa: BLE001 - keep going
            cache[d] = {"name": None, "gender": 0, "error": str(e)[:120]}
        done += 1
        if done % 25 == 0:
            cache_path.write_text(json.dumps(cache, indent=1), encoding="utf-8")
            print(f"  {done}/{len(todo)} …")
        time.sleep(delay)

    cache_path.write_text(json.dumps(cache, indent=1), encoding="utf-8")

    male = [d for d, v in cache.items() if v.get("gender") == 2]
    female = [d for d, v in cache.items() if v.get("gender") == 1]
    unknown = [d for d, v in cache.items() if v.get("gender") not in (1, 2)]
    print(f"\nverified: {len(cache)}   male={len(male)}   female={len(female)}   "
          f"unknown={len(unknown)}")
    print(f"cache -> {cache_path}")
    if unknown:
        print("unknown sample:", ", ".join(unknown[:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
