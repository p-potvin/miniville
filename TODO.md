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
- [ ] Perf: optimize day-start plan rebuild before 5k agents (G007)
- [ ] Avatar gallery: run `scripts/build_avatar_gallery.py` to cast 590 residents
      (G:\Gallery for F, G:\Galleries\Celebrities for M; no reuse — deferred males
      need Import-IMDbStarMeter.ps1 pool growth). PuLID tokens ~0.2s/img after 16s load
- [ ] Economy stats: wages vs spending drift, rent, household budgets (v0.5)
- [ ] Relationship graph viz in UI (cytoscape-lite canvas, optional)
