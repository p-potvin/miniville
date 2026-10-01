# Roadmap

## v0.1 — living town (done)

Persona ingest, households/children, jobs, schedules, needs, encounters,
relationships, event ledger, daily chronicle, deterministic replay.

## v0.2 — story depth

Encounter outcomes with consequences (arguments, favors, gossip propagation),
dating→marriage arc, life events (job loss, move, illness), mood-driven
plan deviations.

## v0.3 — narration layer

Local Ollama: resident "inner monologue", vignette prose for notable events,
weekly newspaper recap. Bounded calls; needs operator approval for batches.

## v0.4 — observer UI

FastAPI + minimal frontend: town map (venues/districts), resident cards,
live event feed, chronicle browser, relationship graph viz.

## v0.5 — deep time (in progress)

The town could only grow — births and immigration in, nothing out — so it could
not turn over across years. Closing that loop is the milestone.

- **Mortality (done).** Age-dependent Gompertz hazard rolled once per simulated
  day, calibrated to a US life table (~10 deaths/1000/yr). A death is settled,
  not just recorded: the spouse is widowed, the job is freed for someone else,
  children left with no adult are taken into a new household, and everyone
  close to the deceased carries the memory. `MINIVILLE_MORTALITY_SCALE` raises
  the rate to watch generations turn over in a short run.
- Births, child aging, immigration — done in v0.4.
- **Seasons + holidays** — the next piece. A holiday synchronizes the town into
  the same venues at once, which is where emergent drama comes from; seasons
  give `schedules.py` a reason to vary (school terms, outdoor leisure).
- **Economy** — wages only go up today. Needs prices, scarcity, and business
  failure before "economy stats" means anything.
- **God-mode interventions** — inject a shock (factory closes, fire, festival)
  and watch the town absorb it. Only meaningful once the above exist.
- Multi-town federation — maybe.
