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
- Births and immigration — done in v0.4.
- **Seasons + holidays + birthdays (done).** A 365-day calendar with seasons;
  nine fixed holidays that pull the town into one venue at once (or home), with
  day-off holidays closing workplaces and the holiday crowd mingling freely;
  school breaks; season-weighted outdoor leisure and seasonal weather. Every
  resident now ages on their own birthday and children come of age at 18 —
  previously nobody aged at all (`growth.age_children` was never called).
- **Economy (done, v0.6).** Rent, groceries, meals, shopping and paid leisure
  give the town a cost of living; a household that misses two rent payments
  downsizes to The Flats. Every venue has a business that is credited for what
  customers spend and debited for the wages it pays; a commercial venue that
  bleeds past −$60k closes, lays off its staff, and reopens weeks later.
  `wage_index` drifts with unemployment, so wages no longer only go up. A weekly
  levy on business reserves funds the town and rebates a civic dividend, which
  keeps money circulating. Full write-up: `docs/ECONOMY.md`.
- **God-mode interventions (done, v0.7).** `cli shock closure|fire|festival`
  injects a shock now or on a future day. Disasters lay off staff, evacuate the
  venue and reroute the day's plans; fires injure bystanders and take a repair
  window (`businesses.reopen_day`) instead of the market's cooldown; festivals
  surface through `seasons.holiday_for` as one-day holidays that pull the town
  into one venue. Everything lands in the event ledger (`shocks` table),
  `/api/shocks`, the chronicle and the Gazette.
- Multi-town federation — maybe.
