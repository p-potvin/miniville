# Autopilot pre-context — miniville

- Seed: user invoked `/autopilot` — "until you have completed all written
  goals, at which point you simply need to write more goals and repeat".
  Hands-off; user has explicitly waived the clarification gate for this loop.
- Task status: activation-prompt.
- Desired outcome: complete every goal in `.omx/ultragoal/goals.json` (G001
  aggregate), verify each, then write the next goal set.
- Known facts:
  - Repo `Github Repos/miniville`, branch `autodev`, Python 3.12 venv, SQLite
    `data/miniville.db` at tick 336 (Day 8 00:00), seed "miniville".
  - Narrator chain done: agent → HF (capped $1.50) → Ollama → raw.
  - v0.2 systems live: life lottery, dating arc, gossip (p=0.2), deviations.
  - `omx` CLI at `%APPDATA%\npm\omx.cmd`; ultragoal artifacts exist.
  - ColONEL-KFC + ComfyUI live on remote host Clopeux-Desktop (100.71.101.21);
    avatar story likely partial/blocked — document boundary.
  - No Codex goal tools in this environment; ultragoal driven manually via
    `omx ultragoal complete-goals` + goals.json status fields.
- Constraints:
  - Deterministic sim core; LLM narration never mutates state.
  - No bulk Ollama/HF loops without operator approval; HF hard-capped $1.50.
  - Commits on `autodev` only; ledger entry before replying; no worktrees.
  - VaultWares protocols apply (ROUTER.md).
- Unknowns/open questions:
  - UI framework choice (grill-me normally gates frontend; user waived
    intervention — pick minimal vanilla/HTMX read-only SPA served by FastAPI).
  - Affair/rivalry drama intensity target (bounded p, opt-out via meta flag).
- Likely touchpoints: `src/miniville/*.py`, `scripts/`, `src/miniville/ui/*`
  (new), `docs/`, `.omx/ultragoal/`.
- Scope note: seed is the Autopilot activation prompt, not guaranteed prior
  conversation context.
