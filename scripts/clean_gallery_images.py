r"""Keep only exemplar-usable photos in the celebrity gallery.

An image is kept iff its Tag-Images sidecar says it is a single person
facing the camera and free of quality exclusions (large_text / color_tint).
Everything else — multi-person scenes, nobody, facing away / over shoulder,
quality-invalid — is moved to `<gallery>\.rejected\<identity>\` together with
its sidecar. Nothing is deleted: the quarantine is reversible.

Untagged images (no sidecar) are left alone and reported.

Run:
    .\.venv\Scripts\python.exe scripts\clean_gallery_images.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"G:\Galleries\Celebrities")
QUAR = ROOT / ".rejected"
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
QUALITY_INVALID = {"large_text", "color_tint"}


def sidecar_for(image: Path) -> Path | None:
    for c in (image.with_suffix(image.suffix + ".json"),
              image.with_suffix(".json")):
        if c.is_file():
            return c
    return None


def verdict(image: Path) -> tuple[bool, str]:
    """(keep, reason)."""
    sc = sidecar_for(image)
    if sc is None:
        return True, "untagged"
    try:
        payload = json.loads(sc.read_text(encoding="utf-8"))
        meta = payload.get("image") or {}
    except (OSError, ValueError):
        return False, "unreadable-sidecar"

    try:
        people = int(meta.get("people_count"))
    except (TypeError, ValueError):
        return False, "no-people-count"
    if people != 1:
        return False, f"{people}-people"
    if str(meta.get("Single", "")).strip().lower() != "1 person":
        return False, "not-single"
    portrait = str(meta.get("Portrait", "")).strip().lower()
    tags = {str(t).strip().lower() for t in meta.get("tags", [])}
    if portrait != "facing camera" and "facing_camera" not in tags:
        return False, f"portrait:{portrait or 'unknown'}"
    parts = {str(t).strip().lower() for t in meta.get("body_parts", [])}
    if "face" not in tags and "face" not in parts:
        return False, "no-face"
    bad = tags & QUALITY_INVALID
    if bad:
        return False, "quality:" + ",".join(sorted(bad))
    return True, "keep"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    root = Path(a.root)

    reasons = Counter()
    kept = moved = untagged = 0
    folders_touched = 0
    for d in sorted(p for p in root.iterdir()
                    if p.is_dir() and not p.name.startswith(".")):
        images = [f for f in d.iterdir() if f.suffix.lower() in IMG_EXT]
        if not images:
            continue
        dest = QUAR / d.name
        touched = False
        for img in images:
            keep, why = verdict(img)
            if why == "untagged":
                untagged += 1
                continue
            if keep:
                kept += 1
                continue
            reasons[why] += 1
            moved += 1
            touched = True
            if not a.dry_run:
                dest.mkdir(parents=True, exist_ok=True)
                sc = sidecar_for(img)
                for f in (img, sc):
                    if f and f.is_file():
                        shutil.move(str(f), str(dest / f.name))
        if touched:
            folders_touched += 1

    print(f"{'DRY RUN — ' if a.dry_run else ''}folders touched: {folders_touched}")
    print(f"kept (exemplar-usable): {kept}")
    print(f"quarantined: {moved}")
    print(f"untagged, left alone: {untagged}")
    print("quarantine reasons:")
    for why, n in reasons.most_common():
        print(f"  {why:24s} {n}")
    if not a.dry_run:
        print(f"moved to {QUAR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
