"""How many of the presence-cache celebrities are actually downloaded?"""
import json
import re
from pathlib import Path

GAL = Path(r"G:\Galleries\Celebrities")
presence = json.loads((GAL / "imdb_presence.json").read_text(encoding="utf-8"))
with_photos = {k for k, v in presence.items() if v}
print(f"presence cache: {len(presence)} ids, {len(with_photos)} with photos")

# downloaded = any folder named *_nmXXXXXXX anywhere under the gallery
downloaded = set()
for p in GAL.rglob("*"):
    if p.is_dir():
        m = re.search(r"(nm\d{7,})", p.name)
        if m:
            downloaded.add(m.group(1))
print(f"downloaded nm ids on disk: {len(downloaded)}")

missing = sorted(with_photos - downloaded)
print(f"\nWITH PHOTOS but NOT downloaded: {len(missing)}")
print("  first 20:", missing[:20])
print("  last 20 :", missing[-20:])

nums = [int(i[2:]) for i in missing]
if nums:
    print(f"  nm range: {min(nums)}..{max(nums)}")
    bands = {}
    for n in nums:
        bands[(n // 1000) * 1000] = bands.get((n // 1000) * 1000, 0) + 1
    print("  by 1000-band:", dict(sorted(bands.items())))

have = sorted(with_photos & downloaded)
print(f"\nwith photos AND downloaded: {len(have)}")
