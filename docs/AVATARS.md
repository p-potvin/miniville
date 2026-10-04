# Avatar pipeline — ColONEL-KFC gallery casting (no ComfyUI)

Goal: a real, consistent face per resident for the observer UI, cast from the
operator's face galleries. **Never reuse an identity** — residents with no
unused source are deferred until the galleries grow.

## Layout (all assets on D:)

- `D:\miniville\gallery\<aNNNN_identity>\` — per-resident identity dirs with
  exemplar source images copied from the source galleries
- `D:\miniville\gallery\gallery.db` — ColONEL-KFC-schema SQLite holding the
  antelopev2 embeddings, landmarks, bbox for every copied crop
- `D:\miniville\avatars\aNNNN.jpg` — 256px face-portrait crops served by the
  observer UI at `/avatars/*` (`MINIVILLE_AVATARS_DIR` overrides)
- `D:\miniville\avatar_mapping.json` — resident → identity cast list +
  `deferred_ids` for residents awaiting new gallery members
- `agents.avatar_path` stores the URL (`/avatars/aNNNN.jpg`)

### Result of the purge + recast (Sat, 04 Oct 2026)

Bounded re-ingest (≤12 imgs/folder over 759 folders) grew the gallery to
**900 identities / 22k crops**; `verify_celebrity_gender` resolved 832 of
1,010 (506 male, 338 female). The recast then cast **507 residents, 0
wrong-sex identities**, and the gender auditor (which runs genderage on each
256px portrait) went **73 → 7 mismatched, 32 → 6 unreadable** after portraits
were re-cropped from an exemplar whose own detected sex matches the identity's.

Two adult-gallery leaks were found and fixed on the way:

- 78 casts came from `F:\amd\gallery` (the second adult gallery, 7k dirs) —
  the first purge only knew `G:\Gallery`; `purge_bad_casts.py` now unions both.
- 121 source folders were emptied into `.assets/.head` by the head pipeline;
  their db rows survived and the pool offered them, producing 56 residents
  with an `avatar_path` pointing at nothing. `pool()` now requires the source
  image to exist.

**129 residents are deferred** (all female — the female pool is exhausted at
current gallery size). Growing it needs new identities via
`Import-IMDbStarMeter.ps1`.

## Casting

`scripts/build_avatar_gallery.py` (miniville venv; stdlib + pillow):

- **Both sexes now draw from `G:\Galleries\Celebrities`.** `G:\Gallery` is an
  adult gallery and is banned as a casting source — its 364 casts were
  stripped by `scripts/purge_bad_casts.py` (Fri, 02 Oct 2026).
- Identity sex comes from the verified map (`D:\miniville\celebrity_gender.json`)
  where present, else the identity-level crop vote — never the per-crop
  `face_crops.gender` column. New casts are recorded `src=celebrity:<sex>:<name>`
  so the purge can never confuse them with the banned source.
- Picks the unused identity with median exemplar age nearest the resident,
  copies up to 4 exemplar images + their `face_crops` rows (embeddings and
  landmarks copied verbatim — identical vectors to a `vw reindex-gallery`
  re-embed, zero GPU cost), crops the portrait, sets `avatar_path`.
- `--only-missing` casts only residents whose `avatar_path` is NULL (the
  post-purge recast mode); it also skips identities already worn by another
  resident and merges the cast list instead of overwriting it.

## Gallery growth

Grow `G:\Galleries\Celebrities` via the IMDb importer (NOT TMDb):
`..\ColONEL-KFC\Import-IMDbStarMeter.ps1 -Phase both -StartNm <N>` scans
sequential IMDb ids, downloads up to 150 photos per person, and extracts them
as `<name>_nmXXXXXXX` folders the builder picks up on re-run.

**The binding constraint is tagging, not photos.** Measured Thu, 01 Oct 2026:
275 folders on disk hold 15,659 images, but only 4,264 carry a Tag-Images
sidecar, and **all 112 folders that are missing from `gallery.db` have zero
tag-eligible images**. Nothing can be embedded, verified or cast from them until
they are tagged.

## The pipeline, in order

Every step is resumable and safe to re-run. Run the vision steps with the
**ColONEL-KFC venv** (`ColONEL-KFC\.venv\Scripts\python.exe`) — it has torch,
ultralytics, onnxruntime and insightface.

```powershell
$KFC = "..\ColONEL-KFC\.venv\Scripts\python.exe"

# 1. tag: writes <photo>.jpg.json beside every image that lacks one
#    (vault-commander's tagger; ~2-5 img/s, skips already-tagged)
& $KFC "..\vault-commander\cli\utils\tag_images.py" --input "G:\Galleries\Celebrities"

# 2. embed + pick exemplars + store every eligible crop
& $KFC scripts\reembed_celebrity_gallery.py --root "G:\Galleries\Celebrities" --resume

# 3. decide each identity's sex offline (insightface vote over all its crops)
& $KFC scripts\verify_celebrity_gender.py

# 4. give the mismatched residents a correct-sex identity
.\.venv\Scripts\python.exe scripts\recase_avatars.py
```

`tag_images.py` needs `rapidocr` (for the `large_text` quality tag). It was
missing from both the vault-commander and ColONEL-KFC venvs; installed into
ColONEL-KFC with `uv pip install --python <KFC python> rapidocr` (6 small
packages, no CUDA). It will move genuinely corrupt files to `.invalid/`.

Order matters: the tagger's `Single`/`people_count` tags are what
`tagged_face_eligibility` gates on, and the re-embed is a no-op on a folder that
is not yet tagged.

### Result of the first full run (Thu, 01 Oct 2026)

Ran the whole chain end to end. Tagging 11,647 images took 62 min at ~3 img/s
(9,448 were already tagged; 0 faulty, 0 moved to `.invalid`).

| | before | after |
| --- | --- | --- |
| folders with >=6 eligible images | 133 | **371** |
| identities in `gallery.db` | 163 | **256** |
| face crops | 2,015 | **4,826** |
| exemplars | 949 | **1,535** |
| residents on a wrong-sex identity | 171 | **125** |

47 residents were re-cast onto a correct-sex identity (John Cleese, James
Cameron, Kirk Douglas, Robert Mitchum and Henry Mancini all entered the pool
this way). Identity-level sex vote: 251/254 correct against the TMDB labels.

**The remaining 125 need people who are not in the gallery yet.** Every folder
with >=6 eligible images has now been embedded, so there is nothing left to
extract from the current photo set — the pool only grows by downloading new
identities (`Import-IMDbStarMeter.ps1`). `G:\Gallery` (931 identities) is
an adult gallery and is permanently banned as a casting source.

## Identity sex: how it is decided

The 201-resident bug came from `pool()` reading the **per-crop**
`face_crops.gender` column. Measured against the TMDB labels: per-crop that
column is only **90.3%** accurate, but a majority vote over all of an identity's
crops is **99.4%**, and **100%** after the re-embed below. Decide sex per
*identity*, never per crop.

`scripts/verify_celebrity_gender.py` does that vote offline (insightface
`genderage`, no network). A plain majority is not enough — it voted Nicole
Kidman male 4-2, the exact false-male error behind the original bug — so the
winner must beat the loser by better than 2:1 and ambiguous identities stay
unresolved rather than guessed. Note the tagger's own `face_female`/`face_male`
tags are **only 61%** accurate (it calls Tom Hanks female); do not use them.

## Re-embedding the gallery

`scripts/reembed_celebrity_gallery.py` (ColONEL-KFC venv) re-runs the ArcFace
engine over every tag-eligible photo, picks exemplars with the smart picker
(`face_organizer.smart_picker.select_smart_exemplars`), and stores **every**
kept crop — not just the six exemplars — flagging the picked ones.

Run once, Thu, 01 Oct 2026: crops 949 → **2,015**, exemplars unchanged at 949,
identity-level sex vote 99.4% → **100%** against TMDB, and the one identity it
used to get wrong (Nicole Kidman, M4/F2 over six crops) now votes correctly
(F6/M4 over ten).

`embed_tagged_gallery.py` in ColONEL-KFC does the same job but its gate
(`prob >= 0.90`) was calibrated on curated galleries with big faces — on this
one it rejects 71 of 71 images sampled, so it would rewrite nothing. The
Miniville script uses a sanity floor (`prob >= 0.60`) and lets the smart picker
choose.

## Optional later steps

- **PuLID identity tokens** (for consistent regenerations): ColONEL-KFC venv,
  `face_organizer.standalone_pulid_flux` `extract` → per-identity `.safetensors`
  (benchmark: 16s model load, ~0.2s/img warm on the RTX 3060 → ~3 min for 590).
- **FLAME heads**: `vw new-face-head -Gallery D:\miniville\gallery -Identity <dir>`
  for a rigged 5023-vertex head per resident; `vw face-mesh` for sparse mesh.
