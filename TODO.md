# TODO

- [x] Narration: self-narration loop via digest + narrate-write (agent-authored)
- [x] Narration providers: HF Inference ($1.50 cap) → Ollama → raw fallback
- [x] v0.2: life events (hire/fire/sick/move), dating arc, gossip, mood deviations
- [x] Favor-asking/IOU debts + work_buddy + affair/betrayal/separation drama
- [x] `backup` (rotated snapshots + export/day-NNN.json) + `daily` routine
- [x] Web observer: `serve` — feed/venues/residents/chronicle/bonds/IOUs (:8787)
- [x] `benchmark` + 2.4k scale test (mean 158ms, p99 2.8s on day-start rebuild)
- [x] Mood balance: social decay 0.5→0.35, home trickle (lonely 129→0 in soak)
- [x] `break`/`eat` night-shift coverage: wrap-around shifts work, mid break
      wraps mod-48, post-shift eat fires before the sleep check
- [x] Perf: 5k day-start 41.3s->3.2s — idx_agents_household killed the
      per-household agents scan in collect_rent (34.8s->73ms)
- [x] Avatar gallery: 415/590 cast to D:\miniville (PuLID tokens + FLAME heads;
      175 males deferred — grow Celebrities via Import-IMDbStarMeter.ps1, re-run builder)
- [x] Perf: day-start rebuild cached + executemany — 2.4k mean 158→49ms, p99 2796→460ms
- [x] Seasons + holidays + birthdays (v0.5): 365-day calendar, seasons, 9 holidays,
      school breaks, holiday crowds, per-agent birthdays + coming of age
- [x] Economy (v0.6): rent/groceries/meals/shopping prices, business P&L + failure,
      wage dynamics, weekly levy + civic dividend, `economy` cmd + UI tab (docs/ECONOMY.md)
- [x] God-mode shocks (v0.7): `shock`/`shocks` CLI — closure & fire lay off staff,
      evacuate the venue, reroute the day; fire injures bystanders + reopen_day
      repair window; festivals ride the holiday machinery (`holiday_for`);
      `/api/shocks` + Gazette sections; cohabitation guard in life.py
- [x] Relationship graph viz in UI — bond wheel (`/api/graph/<id>`, Pixi radial)
- [x] Labour market (v0.8): `jobs.py` — venue staffing targets from capacity +
      traffic scaled to a 92% employment rate, weekly hiring pass, voluntary
      turnover, retirement at 65, `rebalance-jobs` migration. Fixed the venues
      that could never be staffed (`open_workplaces` was kind='workplace' only),
      the Town-Hall monoculture (348 of 435 jobs), and immigration's
      job-holding minors
- [ ] Soak the labour market over 2 years and compare with the pre-fix run
      (baseline: jobs 435->404, unemployment 13.1%->19.7%)
- [x] Inhabitant Individualism (v0.15): 5-axis temperaments, chronotypes, signature knacks (`skills_json`), situated personal happenings (`craft_breakthrough`, `work_mishap`, `hobby_triumph`, `diy_fix`, `repair_bill`, `good_turn`, `contagion`, `caregiven`, `epiphany`), favorite third places, rival workplace avoidance, `fate`/`traits`/`monologue` CLI
- [x] Personal Ambitions & Individualism UI (v0.16): stateful adult life goals (`find_a_job`, `save_nest_egg`, `climb_the_ladder`, `find_true_love`, `pillar_of_the_community`, `make_peace`) that steer schedules/spending/careers/romance, Observer UI **Individualism** tab, `/api/ambitions`, resident card temperament & ambition bars
- [x] 5k-Agent Scale Optimization & Sync Queue (v0.17, `G007-5k-scale`): added `idx_agents_home`, `idx_jobs_place`, `idx_places_kind_district`, `idx_agent_state_place`, `idx_relationships_b`, bulk `collect_rent`, cached `_plan_ctx` holiday/venue lookups — 5,096-resident day-start 11.75s -> 2.78s (4.2x faster), regular ticks 313.5ms p50; added `cli sync` and Operator Priority Queue in `docs/AGENT_SYNC.md`
- [x] Intergenerational Mentorship, Skill Inheritance & Household Traditions (v0.18): `mentorships` table, skill inheritance into `skills_json` + mastery wage bump on graduation, `mentor_a_protege` ambition, Sunday household traditions (`households.tradition`), `cli mentorships`
- [x] Town-Wide Social Network Graph & Mentorship UI (v0.19): `/api/network` district-clustered Pixi graph on Bonds tab with `All`/`Mentorships`/`Romance & Kin`/`Rivalries` filters + click-through to resident ego wheel; Mentorships & Household Traditions tables on Individualism tab
- [x] Personal Keepsakes, Craftsman Creations & Family Heirlooms (v0.20): `keepsakes` table with provenance (`history_json`), artisan creation on breakthroughs/triumphs, gifting (`keepsake_gifted`), estate heirloom inheritance (`heirloom_inherited`, `generations += 1`), `create_masterwork` ambition, `cli keepsakes`, `/api/keepsakes`
- [x] Neighborhood Micro-Events, Mutual Aid & Civic Watch (v0.21): `district_watch` table, theft-triggered `watch_patrol` boosting `crime.py` catch odds, strictly conserved `$12–$28` neighbour `mutual_aid` transfers, Saturday `block_party` & porch swaps, `cli neighborhoods`, `/api/neighborhoods`
- [x] Personal Life Milestones, Anniversaries & Retirement Send-Offs (v0.22): conserved `$150–$350` `coming_of_age_gift` from parents, decade `milestone_birthday` gatherings (`30..90`), annual `wedding_anniversary` romance renewal + keepsake exchange, conserved workplace `retirement_sendoff` bonus + engraved timepiece, `cli milestones`, `/api/milestones`
- [x] Town Cultural Commons: Library Shelf, Community Gallery & Recipe Exchange (v0.23): `keepsakes.venue_id` & `appreciations`, public exhibition at Library/Community Center/Tavern/Diner, visitor appreciation warming creator ties, `acclaimed_work` events, 9th ambition `publish_or_exhibit`, `cli culture`, `/api/culture`
- [x] Out-of-Town Roots, Hometown Correspondence & Personal Diaries (v0.24): `diary_entries` table, `letter_home` & `care_package` events tied to `origin_city, origin_state`, `shared_roots` bonding + `encounters.py` state pairing bonus, first-person milestone diaries, `cli diary`, `/api/diary`
- [x] Seasonal Town Competitions, Craft Fairs & Blue-Ribbon Honours (v0.25): `competitions` table, 6 seasonal competition categories scored from skills/mentorships/keepsakes, conserved `$50` Blue-Ribbon prize from `town_account`, 10th ambition `win_blue_ribbon`, `cli competitions`, `/api/competitions`
- [x] Civic Projects, Participatory Petitions & Durable Town Landmarks (v0.26): `civic_projects` table, 6 district landmarks, strictly conserved donor & municipal funding to trades contractor, volunteer shifts, permanent venue capacity & district cohesion upgrades, 11th ambition `champion_civic_project`, `cli projects`, `/api/projects`
- [x] Personal Health, Athletic Vitality & Hospital Medical Rounds (v0.27): `agent_state.vitality` column, mean-reverting athletic vitality curve buffering Gompertz hazard (`vitality_hazard_multiplier`), clinician `medical_treatment` & `wellness_checkup` rounds at Miniville General Hospital, `cli health`, `/api/health`
- [x] v0.28: Household Pet Companions & Dog-Walking Encounters (`pets.py`)
- [x] v0.29: Weekend Flea Market & Secondhand Keepsake Trade (`flea_market.py`)
- [x] v0.30: School Youth Discovery & Conserved Civic Scholarships (`youth.py`)
- [x] v0.31: Town Historical Archives, Cultural Eras & Hall of Fame (`history.py`)
- [x] v0.36: Volunteer Fire Brigade, Safety Inspections & Rebuild Drives (`fire_brigade.py`)
- [x] v0.37: Town Book Club, Serialized Reading Circles & Author Signings (`book_club.py`)
- [x] v0.38: Home Renovations, Front-Porch Additions & Heirloom Gardens (`renovations.py`)
- [x] v0.39: Annual Town Founders' Jubilee, Time Capsules & Key to Miniville (`jubilee.py`)
- [x] v0.40: Town Theater Troupe & Playbill Productions (`theater.py`)
- [x] v0.41: Neighborhood Tool Library, Seed Bank & Community Orchard (`commons_library.py`)
- [x] v0.42: Municipal Historical Plaques & Heritage Walking Tours (`plaques.py`)
- [x] v0.43: Resident Sabbaticals, Personal Memoirs & Town Masterclasses (`sabbaticals.py`)
