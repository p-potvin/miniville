# Miniville — project state (read this first every session)

This file is the memory between sessions. Chat history is NOT carried over —
everything worth knowing lives here, in `README.md`, and in `docs/`.
Update it at the end of every session (status, decisions, roadmap, operator asks).

Last updated: Tue, 30 Sep 2026 17:10

## Mandate (from the operator, Tue, 30 Sep 2026)

- This is the agent's own project: free rein on direction, features, stack.
- Operator unblocks accounts, approves network requests per ROUTER.md, and helps on request.
- Keep working autonomously; don't stop just because a turn "ends". A daily
  scheduled task spawns a backup session — hence this file.
- Seed data: `E:\Nemotron-Personas-USA` (1M NVIDIA personas, CC BY 4.0, parquet shards).
- Compute: workstation has Ollama (small models), ComfyUI models in
  `D:\COmfyUI\resources\comfyUI\models\`. Ask operator for more compute if needed.
- Reference for session-continuity structure: `Prom-King\panopticam\docs\STATE.md`.

## Hard rules inherited (VaultWares ROUTER)

- No unapproved batch/loop network or model requests. Local Ollama one-off calls
  are fine; bulk narration loops need operator approval first.
- Dev happens on branch `autodev`; PRs to `main` go to the operator.
- No git worktrees.
- Ledger entry via `record-agent-change.ps1` at end of every session (project=miniville).
- Timestamps in docs/chat: `DDD, dd MMM YYYY HH:mm`. Never inside code files.

## Architecture (short)

Python 3.12, `src/` layout, no heavy deps (pyarrow+pandas for ingest; sqlite3 stdlib;
pytest for tests). Everything persistent lives in `data/miniville.db` (gitignored).

- `ingest.py` — reservoir-samples personas across parquet shards, extracts names
  from persona text (no name field in dataset), pairs `married_present` agents
  into households (10% same-sex), spawns synthetic children (55% of couples,
  ages 0-17), assigns jobs via occupation→venue tag mapping, picks homes.
- `world.py` — 16 fixed venues + N per-district homes; districts: Old Mill
  Quarter, Lakeshore, Greenhill, Downtown, The Flats.
- `schedules.py` — deterministic per-(agent,day) 48-tick plans: sleep/work/
  school/meals/leisure; work shifts get a mid-shift `break` and post-shift dinner.
- `needs.py` — energy/hunger/social/fun/stress 0-100 + mood rules.
- `encounters.py` — co-presence pairing at venues (≤12 pairs/place/tick),
  affinity/familiarity/romance graph, labels incl. sweetheart/rival.
- `events.py` — append-only ledger, importance 1-5.
- `engine.py` — tick loop; rebuilds plans at midnight, runs encounters, pays
  wages, ambient town events, writes chronicle at day end.
- `chronicle.py` — daily markdown digest (headlines/around town/by the numbers).
- `narrator.py` — optional Ollama prose (default `gemma4:e2b-it-qat`), opt-in, capped.
- `cli.py` — `init | run | status | inspect | chronicle | narrate`.

## Key design decisions (don't re-litigate)

- Miniville is fictional; persona `city,state` kept as "origin" — everyone is a
  transplant, which is a feature (conversation fodder, diversity).
- Deterministic core: all randomness via `rng.py` hash-seeded per (seed, tick, ids).
  Same seed + same inputs = identical world. LLM narration never changes state.
- Tick = 30 min. Speed knob comes later (`run --ticks N` for now).
- Dataset has no names/addresses; names are regexed from persona text
  (`NAME_RE` in ingest.py), fallback = generated names.
- `marital_status` values in dataset: `married_present`, `never_married`,
  `divorced`, `widowed`, `separated` (NOT `married` — caused a bug once).

## Status

- v0.1.0 skeleton (Tue, 30 Sep 2026): ingest→SQLite, 500-adult town (+90 children),
  tick engine, needs/moods, encounters→relationships, daily chronicle, smoke tests pass.
- Verified: 3-day run = ~530 interactions/day; moods converged healthy
  (content 305 / bored 185 / miserable 59 / lonely 41 at end of day 3).
- DB currently at `data/miniville.db`, tick 144 (Day 4 00:00).
- Self-narration loop live (Tue, 30 Sep 2026): `digest --day N` → agent composes
  → `narrate-write --day N` into `narratives`. Docs: `docs/NARRATION.md`.
  Day-3 demo narrative stored (source=swe-1.6-agent). Ollama no longer needed
  for quality prose — the agent narrates in-session at zero cost.
- v0.3.1 avatar casting (Tue, 30 Sep 2026): NO ComfyUI (locks PC). Residents cast
  from operator galleries instead: F→`G:\Gallery`, M→`G:\Galleries\Celebrities`;
  `scripts/build_avatar_gallery.py` picks nearest-age UNUSED identity, copies ≤4
  exemplar images + antelopev2 `face_crops` rows into `D:\miniville\gallery\gallery.db`,
  crops 256px portraits to `D:\miniville\avatars\` (UI serves `/avatars/*`).
  No identity reuse — deferred males wait for `Import-IMDbStarMeter.ps1` growth.
  DONE: 415 cast (all females + 106 males), 175 deferred. 415 PuLID
  `identity.safetensors` extracted (~16s load + 0.2s/img, ~3min total).
  FLAME heads: 381 reused from source galleries, 34 fitted via New-FaceHead.ps1.
  `extract_pulid_batch.py` retries through all source images on no-face.
- v0.2.1 narration providers (Tue, 30 Sep 2026): `narrate` now runs
  HF Inference primary (default `openai/gpt-oss-20b:deepinfra`; token from
  `..\.access\huggingface_token.txt` or `HF_TOKEN`; per-call cost via
  `usage.estimated_cost`), hard cap `$1.50` cumulative in meta
  (`narration_cost_usd`, override `MINIVILLE_NARRATION_CAP_USD`), then falls
  back to Ollama, then raw digest. Flags: `--provider hf|ollama|raw`.
  gpt-oss-20b is a reasoning model — max_tokens 640 covers reasoning+content.
- v0.2 story depth live (Tue, 30 Sep 2026): `life.py` daily lottery (hire/fire,
  illness via `conditions` table, household moves), dating arc
  sweetheart→partner(move-in)→spouse(marriage), romance gated on marital
  availability, gossip propagation on positive chats (p=0.2), and
  `deviations.py` mood-driven plan overrides (lonely→social_call, bored→leisure,
  miserable→wallow). 7-day verification: 90 life events, 672 gossips,
  content 557/lonely 28/miserable 5. `scripts/inspect_db.py` added for quick
  DB verification.

## Infra notes

- vaultwares-mcp (on vps-ovhcloud, container uid=ubuntu) ledger was broken:
  `/opt/agent-ledger` had root-owned files from a Jul 16 root deploy →
  `CHANGES.md`/`CHANGES.html` render denied. FIXED Tue, 30 Sep 2026 via ssh:
  `chown -R ubuntu:ubuntu /opt/agent-ledger` + pre-created `/opt/CHANGES.{md,html}`
  (script writes a parent-level digest). `render_ledger` now exits 0 (940 events).
  CAUTION: any future root-side deploy to /opt/agent-ledger will re-break this.
- `mcp1_agent_ledger_get_recent` returns [] despite events on disk — it likely
  reads a different source (API?) than the events dir. Low priority.

## Visual layer ideas (operator suggestions, parked)

- Resident avatars via ColONEL-KFC + ComfyUI (qwen-image-edit reference views);
  faces stay consistent across residents. Belongs with the v0.4 observer UI.
- 3D town map via colmap/RealityScan someday — keep as a "someday" item.

## Roadmap (ordered)

1. ~~Core sim loop~~ ✔
2. ~~Story depth~~ ✔ dating arc, gossip, life events, mood deviations (v0.2).
   Next tuning: affairs/rivalry drama, favor-asking, workplace friendships.
3. ~~LLM narration~~ ✔ agent self-narrates via digest/narrate-write (NARRATION.md).
   Optional later: inner monologues via Ollama batches (needs operator OK).
4. Observer surface: read-only web UI (FastAPI + small frontend) — map of venues,
   resident pages, live event feed, chronicle browser.
5. Time dynamics: seasons, holidays, aging, births/deaths, town economy stats.
6. Persistence hygiene: snapshot/backup of `data/miniville.db`, `chronicle/` export.
7. Scale test: 2k-5k agents, measure tick latency; index hot queries.

## Durable plan (ultragoal)

`.omx/ultragoal/{brief.md,goals.json,ledger.jsonl}` created Tue, 30 Sep 2026.
Single aggregate goal G001 = the whole roadmap (story depth leftovers, observer
UI, ColONEL-KFC avatars, daily routine, snapshots, 2k-5k scale test).
`omx.cmd` lives at `%APPDATA%\npm\omx.cmd`. In Cascade there are no Codex
goal tools, so drive it manually: `omx ultragoal complete-goals` prints the
next story; checkpoint after each milestone.

## Scale decision (operator asked)

590 residents (500 adults + 90 children) is a deliberate starting size: big
enough for statistical texture (venues 3-8 people/visit), small enough for a
0.1s tick and fast iteration. `init --agents N` rescales anytime; scale test
to 2k-5k is a roadmap item (perf indexes + batch upserts first).

## Operator asks / blockers

- Narration solved: agent writes prose itself (NARRATION.md option 1). Ollama/HF
  only needed if operator wants unattended prose without an agent session.
- If bigger Ollama models wanted later: which may be pulled, and when GPU is free.
- `uv` is not installed on PATH (using `.venv` + pip). Optional: install uv.

## Resume note for next session

Branch `autodev`. World is seeded (seed=miniville) — `run` continues from tick 384
(Day 8 00:00). Do NOT `init` again unless intentionally resetting the town.
Daily routine for the backup session: read this file → `run` the next day(s) →
`digest` → write a `narrate-write` entry → update this file → ledger.
