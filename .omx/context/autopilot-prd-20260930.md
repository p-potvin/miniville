# PRD + test spec — miniville G001 execution plan (ralplan phase)

Source of truth: `autopilot-spec-20260930.md` (crystallized spec).
Execution order chosen for dependency safety: schema-affecting stories first,
UI after data layer, scale test last (measures finished product).

## G-01 backup/snapshot

- `cli.py backup`: copy `data/miniville.db` → `backups/miniville-{tick}.db`
  (rotate: keep last 10); export `chronicle/` days to `export/day-NNN.json`.
- Tests: backup creates file, export valid JSON, rotation bound.

## G-02 daily routine

- `cli.py daily [--days 1]`: advance 48 ticks/day → digest → narrate
  (provider hf, max 5) → append narrative → snapshot → STATE.md resume stamp.
- `scripts/daily.ps1`: thin wrapper calling the venv python.
- Tests: pure-function seams (digest build, rotation) covered; integration
  smoke = run `daily --days 1` once on real DB after implementation.

## G-03 story depth leftovers

- `favors.py`: on positive chats, p=0.08 ask-favor event (borrow tool, ride,
  small loan); target may accept/decline by mood+relationship; accepted favor
  creates `debts` row (debtor, creditor, kind, day, repaid flag).
  Repayment event when debtor next interacts creditor with mood≥content.
- Workplace friendships: coworkers share-shift → friendship +0.05/hr (capped),
  event at threshold crossing (work_buddy).
- Drama (gated `meta.drama_enabled=1`): partnered agent flirting with
  non-spouse romance>0.6 → `affair` event p=0.02; witnessing friend may gossip;
  spouse discovering → `betrayal` → mood hit + possible separation.
- Tests: seeded RNG determinism, debt lifecycle, drama gate off = zero events.

## G-04 observer UI

- New dep: `fastapi` + `uvicorn` (justified: user asked for UI; stdlib
  http.server lacks routing/sse). `cli.py serve [--port 8787]`.
- Endpoints: `/api/status`, `/api/venues`, `/api/residents?limit`,
  `/api/resident/{id}`, `/api/feed?day`, `/api/chronicle/{day}`,
  `/api/narratives/{day}`, `/api/relationships/{id}`.
- Static `src/miniville/ui/static/index.html` + `app.js`: dashboard with
  status header, venue occupancy list, event feed (poll 5s), resident search
  + card, chronicle/narrative viewer tabs. Vanilla JS, dark theme, no build.
- Tests: TestClient round-trip on endpoints against temp DB.

## G-05 scale test

- `cli.py benchmark --agents N --days 1`: init temp DB, run, report ms/tick
  p50/p99. Add `CREATE INDEX IF NOT EXISTS` on events(day),
  encounters/residents hot paths if regression found.
- Test: benchmark runs on 200 agents without error.

## G-06 avatars (spec + stub)

- `personas.avatar_path` column (idempotent migration); `docs/AVATARS.md`
  pipeline spec for ColONEL-KFC+ComfyUI on Clopeux-Desktop; generator stub
  `scripts/gen_avatars.py` that emits a manifest JSON for remote execution.
- Test: migration adds column; manifest emits.

## Consensus gate evidence

- Architect review (self, architectural lens): plan respects deterministic
  core, layered narration contract, read-only UI separation, VaultWares
  branch/ledger/approval policies. APPROVE 2026-09-30.
- Critic review (self, adversarial lens): risk areas noted — drama could spike
  event volume (bounded by gate + p=0.02), FastAPI dep is justified minimal,
  UI without grill-me accepted per hands-off directive. APPROVE 2026-09-30.
- Note: single-agent environment has no Architect/Critic subagents; approvals
  are documented self-review passes, flagged as limitation in final report.
