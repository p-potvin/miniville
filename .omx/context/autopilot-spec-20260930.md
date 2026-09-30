# Crystallized spec — miniville G001 (deep-interview resolution)

Interview-complete rationale: operator invoked `/autopilot` with explicit
hands-off directive ("until you have completed all written goals ... write
more goals and repeat"). Ambiguity resolved by unilateral decision per
Execution Policy; material choices recorded here.

## Decisions taken without asking

- **UI stack**: FastAPI + vanilla JS single page (no build step), HTMX-style
  fetch + DOM. Rationale: read-only observer, zero deps beyond `fastapi`
  + `uvicorn`; a React build chain would be overkill for v1.
- **Drama budget**: affairs/rivalries opt-in via `meta.drama_enabled` (default
  ON, p small ~2%/qualifying encounter); operator can disable without code.
- **Daily routine**: single `daily.ps1`/`daily` CLI command that advances one
  day, prints digest, calls `narrate --provider hf` (bounded ≤5 calls), appends
  STATE.md timestamp line. No scheduler installed — operator/agent runs it.
- **Avatars (ColONEL-KFC)**: remote GPU host required (Clopeux-Desktop). This
  story ships the *integration spec + schema* (`personas.avatar_path` column,
  generator script skeleton) but actual generation is deferred/blocked pending
  operator's go on remote host automation.
- **Scale test**: run on a copy DB (`MINIVILLE_DB`), 2k agents × 1 day, measure
  tick latency; add indexes if p99 > 500ms. 5k only if 2k comfortable.

## Goal decomposition (from G001 aggregate)

1. G-01 Snapshot/backup: `backup` CLI cmd + `chronicle/` JSON export.
2. G-02 Daily routine: `daily` CLI cmd + `scripts/daily.ps1` wrapper.
3. G-03 Story depth leftovers: favor/debt mechanic, workplace friendships,
   bounded drama (rivalry/affair) — all seeded, importance-tagged events.
4. G-04 Observer UI: FastAPI app `miniville.ui` — /api/venues /api/residents
   /api/feed /api/chronicle /api/narratives + static index.html dashboard.
5. G-05 Scale test: `benchmark` CLI cmd, indexes on events(day), residents,
   relationships; report.
6. G-06 Avatars: schema + spec doc + generator stub; generation deferred.

## Done criteria per goal

Each goal: implemented, `pytest -q` green, smoke-verified against real DB,
STATE.md updated, committed to `autodev`.
