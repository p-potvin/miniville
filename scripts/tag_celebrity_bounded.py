r"""Bounded tag + embed pass for the IMDb-grown celebrity gallery.

The full pipeline tags every image (~100/folder) — too heavy for tonight. This
picks at most --per-folder evenly spaced, still-untagged images from every
celebrity folder that has no completed embed yet, tags them with the
vault-commander TaggerEngine (single model load), then hands off to
reembed_celebrity_gallery.py so each identity gets up to ~a dozen faces instead
of a hundred.

Run with the ColONEL-KFC venv (ultralytics + insightface live there):
    <KFC>\.venv\Scripts\python.exe scripts\tag_celebrity_bounded.py \
        [--per-folder 12] [--skip-embed]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(r"G:\Galleries\Celebrities")
STATE = ROOT / "reembed-state.json"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

VC_UTILS = Path(
    r"C:\Users\Administrator\Desktop\Github Repos\vault-commander\cli\utils")
KFC = Path(r"C:\Users\Administrator\Desktop\Github Repos\ColONEL-KFC")
for p in (str(VC_UTILS), str(KFC)):
    if p not in sys.path:
        sys.path.insert(0, p)


def pick_images(folder: Path, k: int) -> list[str]:
    """Up to k untagged images, spread evenly through the folder for variety."""
    imgs = sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    untagged = [p for p in imgs
                if not any((p.parent / f"{p.stem}{s}").exists()
                           for s in (".json", ".meta.json"))]
    if len(untagged) <= k:
        return [str(p) for p in untagged]
    step = len(untagged) / k
    return [str(untagged[int(i * step)]) for i in range(k)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-folder", type=int, default=12)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--skip-embed", action="store_true")
    args = ap.parse_args()

    done = set()
    if STATE.is_file():
        done = set(json.loads(STATE.read_text(encoding="utf-8"))
                   .get("completed", []))
    folders = [d for d in sorted(ROOT.iterdir())
               if d.is_dir() and not d.name.startswith(".")
               and d.name not in done]
    picks = {d: pick_images(d, args.per_folder) for d in folders}
    total = sum(len(v) for v in picks.values())
    print(f"folders needing an embed: {len(folders)}; "
          f"images to tag: {total} (cap {args.per_folder}/folder)")

    from image_tagger import TaggerEngine          # noqa: E402
    engine = TaggerEngine(device=None)
    n_ok = n_fail = 0
    for i, (folder, paths) in enumerate(picks.items(), 1):
        if not paths:
            continue
        try:
            engine.process_directory(
                target_dir=str(folder), force=False,
                batch_size=args.batch_size, only_paths=paths,
                move_invalid=False)
            n_ok += 1
        except Exception as e:                     # noqa: BLE001
            n_fail += 1
            print(f"  [fail] {folder.name}: {e}")
        if i % 25 == 0 or i == len(picks):
            print(f"  tagged {i}/{len(picks)} folders "
                  f"({n_fail} failures)", flush=True)

    if not args.skip_embed:
        import subprocess
        print("\n=== embedding eligible crops ===")
        here = Path(__file__).resolve().parent
        return subprocess.run(
            [sys.executable, str(here / "reembed_celebrity_gallery.py"),
             "--root", str(ROOT), "--resume"]).returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
