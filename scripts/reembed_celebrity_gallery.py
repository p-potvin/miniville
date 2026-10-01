r"""Re-embed the mixed celebrity gallery: smart exemplars + every eligible face.

Why this exists
---------------
`embed_tagged_gallery.py` in ColONEL-KFC does this job, but its quality gate
(`prob >= 0.90 and feature_norm >= 16`) was calibrated on curated galleries with
big faces. On the IMDb-derived celebrity gallery the faces are small and almost
nothing passes: measured over Nicole Kidman and Tom Hanks, **zero** of 71 images
survived, so the tool would rewrite nothing.

The thresholds here are tuned to this gallery. They are only a *sanity* floor
(reject detector noise, not pick winners); the smart picker does the real
selection afterwards.

What it does, per identity folder:

  1. keep only images the tagger attests are one person with a visible face
     (`face_organizer.identity_manager.tagged_face_eligibility`);
  2. run the ArcFace engine over each and keep the ones above the floor;
  3. pick `--target-count` exemplars with the smart picker
     (`face_organizer.smart_picker.select_smart_exemplars`);
  4. rewrite the identity's `face_crops` with **every** kept crop, flagging the
     picked exemplars.

Storing every crop (not just the six exemplars) is the point: the sex of an
identity is then decided by a vote over all of its faces. Measured against the
TMDB labels, the per-crop `gender` column is only 90.3% accurate, but the
identity-level vote is 99.4% — and the one identity it still gets wrong, Nicole
Kidman (M4/F2 over her six exemplars), votes correctly (F6/M4) once her ten
eligible photos are all present.

Run with the ColONEL-KFC venv (insightface lives there):
    <KFC>\.venv\Scripts\python.exe scripts\reembed_celebrity_gallery.py \
        --root "G:\Galleries\Celebrities" --resume
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

KFC = Path(r"C:\Users\Administrator\Desktop\Github Repos\ColONEL-KFC")
if str(KFC) not in sys.path:
    sys.path.insert(0, str(KFC))

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# sanity floor only — the smart picker chooses; this just rejects detector noise
MIN_PROB = 0.60
MIN_NORM = 12.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--target-count", type=int, default=6)
    ap.add_argument("--min-prob", type=float, default=MIN_PROB)
    ap.add_argument("--min-norm", type=float, default=MIN_NORM)
    ap.add_argument("--min-valid", type=int, default=6,
                    help="skip identities with fewer usable faces than this")
    ap.add_argument("--device", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from face_organizer import FaceEngine, GalleryDB, tagged_face_eligibility
    from face_organizer.smart_picker import select_smart_exemplars

    root = Path(args.root)
    db = GalleryDB(str(root / "gallery.db"))
    engine = FaceEngine(device=args.device)
    state_path = root / "reembed-state.json"
    state = {"completed": []}
    if args.resume and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    done = set(state.get("completed", []))

    print(f"engine contract: {engine.embedding_contract}")
    print(f"root: {root}   already done: {len(done)}")

    folders = sorted(p for p in root.iterdir()
                     if p.is_dir() and not p.name.startswith("."))
    if args.limit:
        folders = folders[: args.limit]

    t0 = time.monotonic()
    for n, folder in enumerate(folders, 1):
        name = folder.name
        if name in done:
            continue
        valid, invalid = [], []
        for image in sorted(p for p in folder.iterdir()
                            if p.is_file() and p.suffix.lower() in IMAGE_EXTS):
            ok, why = tagged_face_eligibility(str(image))
            if not ok:
                invalid.append({"file": image.name, "reason": why})
                continue
            det = engine.extract_face_details(str(image), auto_rotate=True)
            if det is None:
                invalid.append({"file": image.name, "reason": "no_face"})
                continue
            if det.prob < args.min_prob or det.feature_norm < args.min_norm:
                invalid.append({"file": image.name, "reason": "below_floor"})
                continue
            valid.append((image.name, str(image), det))

        if len(valid) < args.min_valid:
            result = {"identity": name, "status": "incomplete",
                      "valid_faces": len(valid), "invalid": invalid,
                      "embedding_contract": engine.embedding_contract}
        else:
            exemplars = select_smart_exemplars(valid, target_count=args.target_count)
            exemplar_names = {x[0] for x in exemplars}
            if not args.dry_run:
                db.clear_crops_for_identity(name)
                for rel, path, det in valid:
                    db.add_face_crop(
                        name, path, os.path.relpath(path, root), det.embedding,
                        det.bbox, det.landmarks_5pts, det.feature_norm, det.prob,
                        rel in exemplar_names, landmarks_106=det.landmarks_106,
                        gender=det.gender, age=det.age)
            result = {"identity": name, "status": "ok",
                      "valid_faces": len(valid), "exemplars": len(exemplar_names),
                      "exemplar_files": sorted(exemplar_names),
                      "invalid": invalid,
                      "embedding_contract": engine.embedding_contract}
        if not args.dry_run:
            (folder / "embedding-evaluation.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8")
            # only a folder that actually produced crops counts as done: an
            # "incomplete" one is usually just untagged, and must be retried
            # once the tagger has run rather than skipped by --resume
            if result["status"] == "ok":
                done.add(name)
                state["completed"] = sorted(done)
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

        if n % 10 == 0 or n == len(folders):
            rate = (time.monotonic() - t0) / max(1, n)
            print(f"  {n}/{len(folders)}  {name[:34]:<34} valid={len(valid):<3} "
                  f"({rate:.1f}s/folder)")

    ok = len(done)
    print(f"\ndone: {ok} identities written   state -> {state_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
