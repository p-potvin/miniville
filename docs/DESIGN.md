# Miniville — design notes

## Concept

A persistent, deterministic small-town simulation. Every resident is a real
synthetic persona from NVIDIA Nemotron-Personas-USA (demographics, occupation,
hobbies, ambitions). The town is an abstract social graph anchored to venues —
"who was where when" drives everything.

## Principles

1. **Determinism first.** All randomness is hash-seeded (`rng.py`). Any tick can
   be replayed and audited. The LLM layer is read-only narration.
2. **SQLite is the world.** One file, WAL mode. Queryable by any tool; no
   in-memory-only state that dies with a session.
3. **Events are the story.** The `events` ledger (importance 1-5) is both the
   sim's memory and the data source for chronicles, narration, and future UI.
4. **Small agent brain, rich soup.** Agents follow schedules + needs; emergent
   story comes from encounters and relationship topology, not per-tick LLM calls.
5. **Grows in complexity.** Planned strata: simulation → narration → observer UI
   → seasons/economy/lifecycle → possibly God-interventions.

## Persona handling

- Adults only in dataset → children are generated (`is_child=1`, simple persona).
- `married_present` agents are paired into couples at ingest; unpaired singles
  get solo households. Source city/state preserved as origin.
- Names parsed from persona text; ~all rows yield "First Last" via `NAME_RE`.

## The world

- 5 districts; 16 civic/public/work venues + generated homes.
- Venues have tags (food, quiet, sport, worship...) and opening ticks.
- Occupation keywords map to venue tags → workplace assignment (see
  `OCCUPATION_MAP`). 8% baseline unemployment.

## Tick semantics

- 48 ticks/day (30 min). Agents sleep 22:30-06:30.
- Weekday work (bitmask in `jobs.work_days`); school for children 5-17.
- Encounters only where agents co-present & awake; ≤12 pairs/venue/tick
  (lifted at an active holiday venue — see Calendar).
- Wages paid at shift end; dining out costs $14.

## Calendar (v0.5, `seasons.py`)

- 365-day years, no leap days; day 0 = Jan 1, Year 1 (a Monday). Seasons are
  meteorological: winter Dec-Feb, spring Mar-May, summer Jun-Aug, autumn Sep-Nov.
- Fixed-date holidays (`HOLIDAYS`) name a venue (or home), a tick window, a
  `day_off` flag and an attendance probability. Attendance is one roll per
  (agent, day) on its own RNG stream, so it never perturbs the plan stream.
  Plan precedence: work shift > school > holiday window > sleep > the rest.
  Day-off holidays close every workplace except `health` venues.
- At the holiday venue during its window every co-present pair gets a chance
  to interact and strangers mix at p≥0.5: a holiday is the town's busiest
  social day, which is the point.
- School breaks: Jun 15-Aug 31 and Dec 22-Jan 2, plus day-off holidays.
- Outdoor leisure (`outdoors`/`water` venues) is kept with a seasonal
  probability (winter 0.35 → summer 1.0); otherwise the agent stays home.
  Ambient weather lines are drawn from the current season.
- Everyone ages on a hash-derived birthday (`birthday_doy`); no ageing on day
  0. A child turning 18 becomes an adult (`coming_of_age` life event).

## Event kinds (v0.1)

`encounter` (tone: hostile..delightful), `relationship` (label transitions),
`town_event` (weather/news), `life_event` (reserved), `world` (reserved).

## Non-goals for now

- No geospatial map/coordinates (venues are nodes, not polygons).
- No pathfinding, no real-time movement animation.
- No external API calls; narrator is local Ollama only.
