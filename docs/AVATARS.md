# Avatar pipeline — ColONEL-KFC + ComfyUI (deferred execution)

Goal: consistent portrait per resident for the observer UI.

## Storage contract

`agents.avatar_path` (added by guarded migration, NULL until generated) holds a
path relative to `assets/avatars/`, e.g. `assets/avatars/a0042.png`. The UI
renders it on resident cards when non-NULL.

## Pipeline (runs on Clopeux-Desktop, 100.71.101.21)

1. `scripts/gen_avatars.py --manifest out.json` emits a manifest listing every
   resident needing a portrait: `{id, name, sex, age, occupation, persona}`.
2. On the GPU host, a driver script consumes the manifest:
   - ComfyUI generates a base portrait per persona (age/sex/occupation-prompted).
   - ColONEL-KFC (InsightFace antelopev2) validates face identity and produces
     the reference-view crops used for consistency across regenerations.
3. Output PNGs land in `assets/avatars/`, `agents.avatar_path` backfilled via
   `scripts/gen_avatars.py --apply <dir>`.

## Status

Blocked on operator approval: remote GPU automation is out of scope for the
local autopilot loop. The manifest stub is ready; generation is a future
story once the operator green-lights the remote run.
