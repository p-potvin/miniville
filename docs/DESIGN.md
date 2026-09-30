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
- Encounters only where agents co-present & awake; ≤12 pairs/venue/tick.
- Wages paid at shift end; dining out costs $14.

## Event kinds (v0.1)

`encounter` (tone: hostile..delightful), `relationship` (label transitions),
`town_event` (weather/news), `life_event` (reserved), `world` (reserved).

## Non-goals for now

- No geospatial map/coordinates (venues are nodes, not polygons).
- No pathfinding, no real-time movement animation.
- No external API calls; narrator is local Ollama only.
