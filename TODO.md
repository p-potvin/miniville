# TODO

- [x] Narration: self-narration loop via digest + narrate-write (agent-authored)
- [x] Narration providers: HF Inference ($1.50 cap) → Ollama → raw fallback
- [x] v0.2: life events (hire/fire/sick/move), dating arc, gossip, mood deviations
- [x] Favor-asking/IOU debts + work_buddy + affair/betrayal/separation drama
- [x] `backup` (rotated snapshots + export/day-NNN.json) + `daily` routine
- [x] Web observer: `serve` — feed/venues/residents/chronicle/bonds/IOUs (:8787)
- [x] `benchmark` + 2.4k scale test (mean 158ms, p99 2.8s on day-start rebuild)
- [x] Mood balance: social decay 0.5→0.35, home trickle (lonely 129→0 in soak)
- [ ] `break`/`eat` activity coverage for night-shift edge cases
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
- [ ] Relationship graph viz in UI (cytoscape-lite canvas, optional)
