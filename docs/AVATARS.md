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

## Casting

`scripts/build_avatar_gallery.py` (miniville venv; stdlib + pillow):

- Female residents → `G:\Gallery` exemplars (gender=0, ~930 identities)
- Male residents → `G:\Galleries\Celebrities` exemplars (gender=1; currently
  ~90 male identities, so ~190 of 281 males defer until growth)
- Picks the unused identity with median exemplar age nearest the resident,
  copies up to 4 exemplar images + their `face_crops` rows (embeddings and
  landmarks copied verbatim — identical vectors to a `vw reindex-gallery`
  re-embed, zero GPU cost), crops the portrait, sets `avatar_path`.

## Gallery growth (male pool)

Grow `G:\Galleries\Celebrities` via the IMDb importer (NOT TMDb):
`..\ColONEL-KFC\Import-IMDbStarMeter.ps1 -Phase both -StartNm <N>` scans
sequential IMDb ids, downloads up to 150 photos per person, and extracts them
as `<name>_nmXXXXXXX` folders the builder picks up on re-run.

## Optional later steps

- **PuLID identity tokens** (for consistent regenerations): ColONEL-KFC venv,
  `face_organizer.standalone_pulid_flux` `extract` → per-identity `.safetensors`
  (benchmark: 16s model load, ~0.2s/img warm on the RTX 3060 → ~3 min for 590).
- **FLAME heads**: `vw new-face-head -Gallery D:\miniville\gallery -Identity <dir>`
  for a rigged 5023-vertex head per resident; `vw face-mesh` for sparse mesh.
