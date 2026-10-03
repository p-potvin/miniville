"""What is G:\\Gallery really, and where did the IMDb scraper get to?"""
import json
import re
import sqlite3
from pathlib import Path

G2 = Path(r"G:\Gallery")
GAL = Path(r"G:\Galleries\Celebrities")

print("=== G:\\Gallery identity names (sample) ===")
dirs = sorted(p.name for p in G2.iterdir() if p.is_dir() and not p.name.startswith("."))
print(f"  {len(dirs)} folders")
with_id = [d for d in dirs if re.search(r"_\d{2,}$", d)]
without = [d for d in dirs if not re.search(r"_\d{2,}$", d)]
print(f"  with numeric id : {len(with_id)}")
print(f"  no numeric id   : {len(without)}")
print("  first 25 with id :", with_id[:25])
print("  first 25 no id   :", without[:25])

print("\n=== which gallery do female residents use? ===")
MAP = Path(r"D:\miniville\avatar_mapping.json")
m = json.loads(MAP.read_text(encoding="utf-8"))
from collections import Counter
srcs = Counter()
for e in m["cast"]:
    gd = e.get("gallery_dir", "")
    src = e.get("src", "") or ("celebrity" if "_nm" in gd else "gallery")
    srcs[src] += 1
print("  cast by source tag:", dict(srcs))

print("\n=== IMDb scraper state ===")
for name in ("imdb_presence.json", "imdb_presence.headless-bad.json"):
    p = GAL / name
    if not p.is_file():
        print(f"  {name}: missing")
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    trues = sum(1 for v in d.values() if v)
    ids = sorted(d)
    print(f"  {name}: {len(d)} ids, {trues} with photos, "
          f"range {ids[0]}..{ids[-1]}")
