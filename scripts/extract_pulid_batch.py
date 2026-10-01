r"""Batch-extract PuLID identity tokens for cast residents.

Runs under the ColONEL-KFC venv (needs torch + insightface):
  & ..\ColONEL-KFC\.venv\Scripts\python.exe scripts\extract_pulid_batch.py

Writes <D:\miniville\gallery\<dir>>\identity.safetensors per cast resident.
"""
import json
import sys
import time
from pathlib import Path

KFC = Path(r"C:\Users\Administrator\Desktop\Github Repos\ColONEL-KFC")
sys.path.insert(0, str(KFC))
from face_organizer.standalone_pulid_flux import StandalonePuLIDExtractor  # noqa: E402

ROOT = Path(r"D:\miniville")
mapping = json.loads((ROOT / "avatar_mapping.json").read_text())["cast"]

ex = StandalonePuLIDExtractor()
done = fail = 0
t0 = time.perf_counter()
for m in mapping:
    d = ROOT / "gallery" / m["gallery_dir"]
    out = d / "identity.safetensors"
    if out.exists():
        done += 1
        continue
    imgs = sorted(d.glob("*.jpg")) + sorted(d.glob("*.png"))
    if not imgs:
        fail += 1
        continue
    ok = False
    for img in imgs:
        try:
            ex.extract_identity(str(img), output_safetensors=str(out))
            ok = True
            break
        except Exception as e:
            print(f"  retry {d.name} ({img.name}): {e}", flush=True)
    if ok:
        done += 1
    else:
        fail += 1
    if done % 25 == 0:
        el = time.perf_counter() - t0
        print(f"{done}/{len(mapping)}  {el:.0f}s elapsed", flush=True)
print(f"done={done} fail={fail} in {time.perf_counter()-t0:.0f}s")
