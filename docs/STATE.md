# Miniville — project state (read this first every session)

This file is the memory between sessions. Chat history is NOT carried over —
everything worth knowing lives here, in `README.md`, and in `docs/`.
Update it at the end of every session (status, decisions, roadmap, operator asks).

Last updated: Sat, 03 Oct 2026 12:30

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
- `encounters.py` — co-presence pairing at venues (≤12 pairs/place/tick;
  uncapped + strangers at p≥0.5 at an active holiday venue),
  affinity/familiarity/romance graph, labels incl. sweetheart/rival.
- `seasons.py` — calendar (day 0 = Jan 1, Year 1; 365-day years), seasons,
  `HOLIDAYS`, school breaks, outdoor appeal, seasonal weather, birthdays.
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
  FLAME heads: 381 reused from source galleries, 34 fitting via New-FaceHead.ps1
  (call it directly — the vw wrapper drops `-Identity` when forwarding).
  `extract_pulid_batch.py` retries through all source images on no-face.
  Pushed to origin/main. IMDb StarMeter scraper running long-term in chunks
  (`Import-IMDbStarMeter.ps1 -Phase both`, 2k-id chunks from nm0000001 up,
  headless, resumable via gallery dirs + presence cache) — goal 10k identities
  with ≥6 photos as back catalog for pool growth. NOTE: must run with CWD =
  ColONEL-KFC root (`imdb_gallery` is a repo-level module, not installed).
- ColONEL-KFC cross-project sync (Wed, 30 Sep 2026): KFC bridge (`Sync-MinivilleAssets.ps1`
  / `face_organizer.miniville_bridge`) unlocked 123 male identities from `F:\amd\gallery`
  and `G:\Gallery`. Cast 167 deferred residents with zero GPU overhead; total cast
  reached 582 / 610 (95.4%). Remaining deferred reduced to 28. Mapping updated in
  `D:\miniville\avatar_mapping.json` and `agents.avatar_path`.
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
- v0.4 memory + growth + newspaper (Wed, 30 Sep 2026): `memory.py` (append-only
  memory stream per agent, importance+recency retrieval, every-3-day reflections),
  `growth.py` (immigration of unused dataset personas + births for high-romance
  spouses), `newspaper.py` (weekly Gazette front page from the chronicle, stored
  in `newspapers`). UI gains a Gazette tab and a Memories block on resident
  cards; new CLI `immigrate | newspaper | reflect`. Verified on the live world:
  9,612 memories, 1,255 reflections, 15 births (pop 590→601), 1 edition.
  Also fixed a latent `NameError` in `chronicle.day_digest` (missing `import json`).
- v0.5 mortality (Wed, 30 Sep 2026): `mortality.py` — age-dependent Gompertz
  hazard (`A=5e-5`, `B=0.085`, calibrated to a US life table: ~1.5/1000 at 40,
  ~19/1000 at 70), rolled once per simulated day, x4 while ill. A death is
  *settled*, not just recorded: the spouse is widowed (agent row + relationship
  label), the job/plans/conditions are released, children left with no living
  adult are rehomed into a new household, and everyone with `familiarity>=30`
  carries a `death` memory. `MINIVILLE_MORTALITY_SCALE` raises the rate to watch
  generations turn over in a short run. The deceased are also excluded from
  `chronicle.day_digest` moods, `memory.reflect_all`, and `cli status`.
  Verified on a copy of the live world: one simulated year (17,520 ticks, ~13
  min) = **4 deaths** (ages 58/70/80/81), 3 widows, 27 mourning memories, 0
  orphans (the dead had no minor children); all 4 deceased held 0 plans/jobs/
  conditions. 8 new tests, 42 total pass.
- v0.5 seasons + holidays + birthdays (Thu, 01 Oct 2026, cloud session):
  `seasons.py` calendar/seasons; 9 fixed holidays (venue or home, tick window,
  `day_off`, attendance p) — day-off holidays close every workplace except
  `health`; plan precedence work > school > holiday > sleep; school breaks
  Jun 15-Aug 31 + Dec 22-Jan 2; outdoor leisure kept with seasonal p (winter
  0.35 → summer 1.0); weather lines per season. BUG FIXED: nobody ever aged —
  `growth.age_children` was dead code (removed). Now everyone ages on a
  hash-derived birthday (`birthday_doy`), children come of age at 18
  (`coming_of_age` life event). Chronicle/digest/status/UI show date + season +
  holiday. Verified on SYNTHETIC worlds only (no dataset/live DB on the cloud
  VM): 590-adult Jul 1-7 soak = 391/742/793/**1,651 (Jul 4)**/771/795/393
  interactions, 44.9 ticks/s on Jul 4; outdoor leisure Jan week 2,402 vs Jul
  week 7,119. Tests: 46 pass (test_ui skipped — no PyPI on that VM).
  NOT yet run on the live world.
  PR #1 review fixes (same day): wages only for agents whose day plan has
  work (was paying on weekends/holidays too); birthdays run before plan
  rebuild; `agents.birth_day` (newborns age on their real birthday);
  memories keep the event's tick; orphans fostered into an adult household;
  immigration reservoir seeded per tick; UI moods exclude the dead; Gazette
  selector refreshes; avatar scripts: no caching of transient TMDB failures,
  face_crops removed with retired identities. 54 tests pass.
- **DUPLICATE WORK — reconciled (Thu, 01 Oct 2026, workstation session).** The
  workstation session (me) independently built a *second* seasons/holidays
  implementation while the cloud session was building the one above; we found
  out on push/pull. **The cloud session's `seasons.py` won** and mine was
  dropped entirely (my `0854e96` is preserved on `backup/economy-local-autodev`).
  His calendar is better: real months, fixed dates, per-agent birthdays.
  Deliberately *not* ported: my `seasonal_tag_weights` — his `OUTDOOR_APPEAL`
  already covers seasonal leisure, and running both would double-count.
  My economy commit was rebased onto his tip; both found and fixed the
  weekend/holiday wage bug independently, and his `engine._wages_and_spending`
  was replaced by `economy.pay_wages` (his `test_wages_require_a_work_plan`
  now calls it). **Lesson: pull before starting a milestone, push when done.**
- v0.6 economy (Thu, 01 Oct 2026): `economy.py` — the town finally has *flows*.
  Full write-up in `docs/ECONOMY.md`; the short version:
  - **Prices.** Weekly rent by district ($290–460, split across adults),
    groceries ($7.50/day), meals out, shopping trips and paid leisure. A new
    `shopping` activity + retail/workplace venues now being valid destinations
    gives the shops customers.
  - **Scarcity.** A household that misses two rent payments is moved to The
    Flats (`rent_arrears` → `downsize`). Observed: 0–4 households in arrears at
    any time, ~1 downsizing/week in a long soak.
  - **Businesses.** Every non-home venue gets a `businesses` row: customer
    spending is credited, wages debited, public-service venues are town-funded
    and never fail. A commercial venue bleeding past −$60k closes, lays off its
    staff, stops being a destination, and reopens after 21 days. Struggling
    venues raise `price_index` (≤1.6×); comfortable ones drift back to 0.85×.
  - **Wage dynamics.** `wage_index` (meta) falls 0.5%/wk above 12%
    unemployment and rises below 5% — wages no longer only go up.
  - **Town books.** Wages are minted and rent destroyed, so a weekly levy (5% of
    business reserves) funds the town and rebates 35% as a civic dividend;
    otherwise money paid to a shop is gone for good and the town bleeds dry.
  - **Two bugs found and fixed.** (a) `daily_life_lottery` applied a flat firing
    rate to the employed and a flat hiring rate to the unemployed, so the town
    steadily shed every job (unemployment climbed 14% → 33% over 120 days); the
    hiring rate is now derived from the firing rate. (b) the wage pass ignored
    `jobs.work_days` and paid every job seven days a week — staff are now paid
    only for days actually worked, with wages raised 7/5 (`economy_v2`
    migration) so weekly income and the town's balance are unchanged.
  - **Migrations.** `economy_v1` (×2.0 wages+balances) and `economy_v2` (×1.4
    wages) are flagged and idempotent in `db.migrate`.
  - **Surfaces.** `cli economy [--days N]`, `GET /api/economy`, an Economy tab
    in the observer UI, an "Economy" section in the chronicle, and the Gazette's
    back page.
  Verified on a copy of the live world over 120 days: money supply flat
  (±$1k/day on ~$9M), median wallet −$20/day, unemployment 12–19%, all
  businesses solvent, moods unchanged (content 726 / miserable 8). 22 new tests.
  After the merge with the cloud session's seasons work: **82 tests pass**.
- v0.7 god-mode shocks (Fri, 02 Oct 2026, workstation session — took over the
  cloud session's claim when it went inactive): `shocks.py` +
  `cli shock closure|fire|festival [--day N] [--days N]` and `cli shocks`.
  Disasters lay off staff, evacuate the venue and reroute the day's remaining
  plans home; fires injure up to 3 occupants and set `businesses.reopen_day`
  (new nullable column, migrated) so a repair window overrides the market's
  21-day cooldown. Scheduled shocks land at day-start via `shocks.apply_due`
  (in `engine._day_start`, after settlement, before plan rebuild). Festivals
  need no mutation: `seasons.holiday_for(conn, day)` builds a one-day Holiday
  from the shock row (tag `festival`, 15:00–20:00, not a day off), so
  schedules/crowd-mingling/`announce_day` treat it like a real holiday —
  festival day ran 1,448 interactions vs ~675. `/api/shocks` lists them;
  `status`/digest show festival days; Gazette learned the shock tags.
  `deviations` no longer routes lonely residents into closed venues. Also took
  the cloud session's second item: `life.py` cohabitation guard
  (`_has_rel_elsewhere`) — no more triple "moved in together", no bigamy.
  Smoke-verified on a live-world copy (fire → 5 layoffs, festival → crowd,
  scheduled closure → lands at day-start). **97 tests pass.**
- **Live demo (Fri, 02 Oct 2026 17:30):** world advanced to tick 1859 (Day 39,
  Feb 8). `shock fire` on Riverside Diner at noon laid off 5, hurt 3 (Victoria
  Armstrong, Adli Caldwell, Natarajan Ansari — sick until t1896); reopens day
  51. `shock festival` called a Lush Meadow Park festival for today — 326
  residents celebrating at 17:30. Observer now binds 0.0.0.0:8787 (LAN +
  Tailscale) with a "Miniville Observer" firewall rule for spectators.
- **Operator calls (Fri, 02 Oct 2026):** IMDb media fetcher stopped for today —
  ~872 gallery folders is enough to fill the wrong-sex backlog; the
  `scan_and_add` ingester keeps running. **3D head/albedo reconstruction is
  deferred** until the observer UI is fully fledged (GPU-heavy, nothing to
  show it on); embed/tag/cast continues on 2D crops.
- **Duplicate cohabitation — true root cause fixed.** `interact()` recomputed
  `_rel_label` each meeting and rom>80 always yields `sweetheart`, demoting
  `partner` back so the arc re-fired; day-37 Laverne Miller had **10** dupes.
  `ARC_LABELS` (partner/spouse/widowed/estranged) now pin in `interact()`.
  Display: couple events read "A and B moved in together" (canonical order)
  and identical lines merge to `(xN)` in feed + chronicle. Regression test in
  `test_shocks.py`. **98 tests pass.**
- **Casting purge (Fri, 02 Oct 2026 evening):** `scripts/purge_bad_casts.py`
  removed **364 casts** sourced from `G:\Gallery` (317 `src=Female` + 47
  wrong-sex `src=Male`) from `D:\miniville\gallery\gallery.db`; the **218
  real-celebrity casts were kept**, `avatar_path` nulled for the purged.
  `G:\Gallery` is permanently banned as a casting source.
- **Bounded ingest running:** `scripts/tag_celebrity_bounded.py` tags ≤12
  evenly-spaced images per unembedded folder (759 folders) via the
  vault-commander TaggerEngine, then chains into
  `reembed_celebrity_gallery.py --resume`. Recast when it lands:
  `build_avatar_gallery.py --only-missing` — the female pool now draws from
  the celebrity gallery too, with sex decided by TMDB map else >60% all-crop
  vote. Male pool is currently 0 (all verified males already worn) — new
  male folders in the embed pass will refill it.
- **Map viz shipped:** Pixi.js **Map tab** in the observer — fixed district
  tiles, venue squares sized by capacity (closed venues dimmed, marked red),
  all 636 residents as activity-colored dots (golden-angle scatter at venues,
  home crowd pooled in the district homes strip), drag/zoom, hover tooltip,
  click → resident card, 5s refresh. `/api/map` endpoint; pixi v7 vendored.
  Next candidates: resident-dot→resident-card already done; sigma.js bond
  graph; per-district heat/trend overlays.

- **AVATAR SEX MISMATCH (found Wed, 30 Sep 2026) — 201 male residents hold a
  female identity.** Root cause: `build_avatar_gallery.pool()` filters source
  identities by `face_crops.gender`, which is unreliable (mislabels angled/
  profile crops), and the KFC bridge pulled from the female-only galleries into
  the male pool. Breakdown: 43 from `G:\Galleries\Celebrities` (female
  celebrities — Hannah Waddingham, Anne Hathaway, Florence Pugh, Kaya
  Scodelario…), 159 from `F:\amd\gallery` / `G:\Gallery`. Zero female residents
  hold a male identity. `G:\Galleries\Celebrities` is MIXED — TMDB-verified
  **153 male / 114 female** of 271. Tooling added: `scripts/verify_celebrity_gender.py`
  (TMDB `/find` by IMDb id or `/person/{id}`, cached to
  `D:\miniville\celebrity_gender.json`) and `scripts/audit_avatar_gender.py`
  (insightface genderage on rendered portraits; report at
  `D:\miniville\avatar_gender_audit.json`). SHORTFALL: 202 residents need a male
  identity but only 30 verified male identities were unused — the re-cast must
  proceed incrementally as the scraper grows the pool.
  FIX IN PROGRESS: `build_avatar_gallery.pool()` now takes a TMDB-verified sex
  map and uses it instead of `f.gender`; `scripts/recase_avatars.py` re-casts
  only the mismatched residents (idempotent, resumable, retires the old
  wrong-sex identity); `scripts/refresh_avatars.py` runs one pass of
  ingest -> verify -> re-cast and is safe on a timer. **30 re-cast so far,
  172 still deferred.** A background loop runs refresh_avatars every 15 min
  (log: `D:\miniville\avatar-refresh.log`).
  NOTE: the scraper only *downloads* into `<gallery>/.imdb-imports/`; the
  ingest into `gallery.db` is a separate step, so a long scrape leaves a
  staging backlog. `ColONEL-KFC\ingest_staged.py` drains it via the same
  `process_staged_identity` the TMDB importer uses.
- IMDb scraper FIXED (Wed, 30 Sep 2026): it was running `-Headless`, and IMDb
  returns **403 Forbidden** to headless Chromium — every page failed, so the
  presence scan reported "no photos" for ~99% of ids (1% yield). Running
  **headed** gives 97% yield (146/150 with photos, 137 media, 0 errors). Now
  running headed in 2k-id chunks; `G:\Galleries\Celebrities` grew ~100 → 268.
  Do NOT pass `-Headless`.

- Birth-rate calibration (Sat, 03 Oct 2026, cloud): `growth.births` was
  P_BIRTH=0.02/day per spouse pair with no age gate, which is about 7 babies a
  year per couple, couples in their 70s included. Ingest makes nearly every
  married persona a romance-80+ spouse pair, so the live town was booming
  ("15 births, 590→601"). Now `ANNUAL_BIRTH_RATE=0.10` per fertile couple
  (daily ≈0.00029), the mother aged 18–44 (or the younger partner for same-sex
  couples), both partners alive adults, and no birth within 365 days of the last
  newborn in the household. The rng stream is unchanged. Expected on the live
  town: a handful of births a year, close to the ~6 deaths/yr from Gompertz.
  Local agent: please measure births per year on a copy of the live DB.
- Deep-time soak (Sat, 03 Oct 2026, cloud): synthetic 300-adult + 40-child
  town, 2 simulated years, script outside the repo
  (`C:/Users/Administrator/soak/soak_year.py`, on the cloud VM only). It ran on
  the pre-calibration code. First ~280 days: no exceptions, about 2 s per
  simulated day, population 340→351, and job counts holding at 208–241. One
  business closed and reopened. Couples moved through sweetheart→partner→spouse
  with no duplicate move-ins, confirming the v0.7 label-pinning fix. Zero
  economy fields in the soak's same-day `economy_days` reads are a measurement
  artifact: `record_day` only finalises day N at the start of day N+1. Not a bug.
  Final: the VM restart killed it at day 476 (of 730): no exceptions,
  population 340→380, jobs 204, no business closed at the end, ~8 s per
  simulated day late in the run. A post-calibration rerun is still owed.

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
5. Time dynamics: ~~seasons, holidays, aging, births/deaths~~ ✔ (v0.4/v0.5);
   town economy stats next (see ROADMAP.md v0.5).
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
- Thu, 01 Oct 2026 session ran on a fresh cloud VM (not the workstation): no
  PyPI (allowlist request for pypi.org + files.pythonhosted.org pending), no
  dataset/live DB/Ollama, vaultwares-mcp SSE unreachable, and
  `record-agent-change.ps1` not present — ledger entry for that session is
  owed; the next workstation session should record it.
- TMDB is retired (operator, Thu, 01 Oct 2026): `refresh_avatars` step 2
  (`verify_celebrity_gender.py`) must be dropped or replaced; local agent owns
  avatars. Two agents now share the repo; coordinate via `docs/AGENT_SYNC.md`.
  DONE (Thu, 01 Oct 2026, workstation): `verify_celebrity_gender.py` is now
  **offline** — insightface `genderage` over each identity's own face crops,
  same cache file and JSON shape, so the builder and re-caster are untouched.
  A plain majority was unsafe (it voted Nicole Kidman male 4-2 — the exact
  false-male error behind the original 201-resident bug), so the winner must now
  beat the loser by better than 2:1 and ambiguous identities stay unresolved.
  Validated on the 162 identities with both a cached label and usable crops:
  34 agree / 0 disagree / 6 refused. Nothing in the avatar path hits the network.
- Ledger debt cleared: the cloud session's entry (owed because its VM had no
  `record-agent-change.ps1`) is recorded on the workstation.
- Celebrity gallery re-embedded (Thu, 01 Oct 2026, workstation). The operator
  suspected the sex mislabels came from a pre-smart-picker gallery with stale
  embeddings. Measured, the opposite is true: **the gallery is already
  smart-picked and the embeddings reproduce exactly** — for Nicole Kidman, Tom
  Hanks and Kaya Scodelario, `select_smart_exemplars(valid, 6)` returns
  precisely the six crops already stored, and a fresh `FaceEngine` run
  reproduces every `quality_score`/`feature_norm` to 3 decimals. The real cause
  of the original bug is that `pool()` read the **per-crop** `gender` column:
  90.3% accurate per crop, but 99.4% as an identity-level majority.
  What *did* need doing, and is now done: `scripts/reembed_celebrity_gallery.py`
  re-ran the engine over every tag-eligible photo and stored **every** kept crop
  instead of only the six exemplars — crops 949 -> 2,015, exemplars unchanged,
  identity-level sex vote 99.4% -> **100%** against TMDB, and Nicole Kidman
  (the one identity it got wrong, M4/F2 over six crops) now votes correctly
  (F6/M4 over ten). `verify_celebrity_gender.py` was fixed to vote over *all* a
  person's crops, not the first six — sampling six left lola_petticrew
  undecided (M3/F11 overall, but M2/F4 over the first six, an exact 2:1 tie).
  `docs/AVATARS.md` records the method and the measurements.
  The binding constraint was *tagging*, and it is now cleared. `rapidocr` was
  missing from both the vault-commander and ColONEL-KFC venvs; installed into
  ColONEL-KFC with `uv pip install --python <KFC python> rapidocr` (6 small
  packages, no CUDA), which the operator had pointed me at as the vision venv.
  Then ran the whole chain (tag -> re-embed -> verify -> re-cast), 62 min of
  tagging at ~3 img/s plus ~15 min of embedding:

  | | before | after |
  | --- | --- | --- |
  | folders with >=6 eligible images | 133 | **371** |
  | identities in `gallery.db` | 163 | **256** |
  | face crops | 2,015 | **4,826** |
  | exemplars | 949 | **1,535** |
  | residents on a wrong-sex identity | 171 | **125** |
  47 residents re-cast onto a correct-sex identity (John Cleese, James Cameron,
  Kirk Douglas, Robert Mitchum, Henry Mancini joined the pool). Identity-level
  sex vote 251/254 correct vs the TMDB labels. Pipeline documented in
  `docs/AVATARS.md`.
  **Remaining 125 need people not in the gallery yet** — every folder with >=6
  eligible images has now been embedded, so the pool only grows by downloading
  new identities (`Import-IMDbStarMeter.ps1`). `G:\Gallery` (931 identities) is
  female-only (929F/1M). Also fixed `reembed_celebrity_gallery.py` marking
  untagged folders "done" (so `--resume` skipped them) and
  `verify_celebrity_gender.py` sampling only the first 6 crops.

## Resume note for next session

Branch `autodev`, both agents pushing. World is seeded (seed=miniville) — `run`
continues from tick 1776 (Day 38 = **Feb 7, Year 1**, winter; next holiday is
Founders' Day, Apr 18 = Day 108, tick 5136). Do NOT `init` again unless
intentionally resetting the town. Both economy migrations are applied (wages
$62–252/day).

**Read `docs/AGENT_SYNC.md` first** — claims and messages between the workstation
and cloud sessions live there. Pull before starting work; push small commits.

CAVEAT: days 18–37 of the live world were simulated under the *pre-merge* seasons
code (my dropped implementation), so a few chronicles say "Spring" where the merged
calendar says winter, and the ledger contains a couple of holiday events that no
longer exist (`Spring Blossom Festival`). Cosmetic only — nothing reads it back.

Open items the next session could take: the two findings left for the cloud agent
in `AGENT_SYNC.md` (duplicate `cohabitation` events; keep the `cli.py` stdout
reconfigure), and the roadmap's next milestone — **God-mode shocks** (inject a
factory closure / fire / festival and watch the town absorb it).
Daily routine for the backup session: read this file → `run` the next day(s) →
`digest` → write a `narrate-write` entry → update this file → ledger.
