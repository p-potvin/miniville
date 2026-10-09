# Miniville — agent entry point

Autonomous town simulation seeded by NVIDIA Nemotron-Personas-USA.

**First, every session: read `docs/STATE.md`** (mandate, decisions, blockers,
resume pointer). Update it before finishing.

**Two agents share this repo** (workstation + cloud Devin). Pull before working,
push small commits to `autodev` often, and coordinate via `docs/AGENT_SYNC.md`
(claims + append-only messages).

VaultWares protocols apply via `vaultwares-docs/instructions/ROUTER.md` —
notably: branch `autodev` (PRs to main for operator), no unapproved request
loops (Ollama narration batches need operator OK), agent-ledger entry before
replying, no git worktrees.

## Commands

```powershell
.\.venv\Scripts\python.exe -m miniville.cli init --agents 500   # bootstrap (resets world!)
.\.venv\Scripts\python.exe -m miniville.cli run --ticks 96      # advance 2 days
.\.venv\Scripts\python.exe -m miniville.cli status
.\.venv\Scripts\python.exe -m miniville.cli economy --days 10   # money, businesses, wages
.\.venv\Scripts\python.exe -m miniville.cli inspect "Name"
.\.venv\Scripts\python.exe -m miniville.cli chronicle 3         # day digest
.\.venv\Scripts\python.exe -m miniville.cli narrate --day 3     # local Ollama prose (bounded)
.\.venv\Scripts\python.exe -m pytest -q
```

Env: `MINIVILLE_DB` overrides `data/miniville.db`. Dataset: `E:\Nemotron-Personas-USA`.

## Long runs are how this project finds its bugs

Every real defect so far was **cross-system** and invisible to unit tests:
rent destroyed while public payroll was minted; children eating only dinner
because the school branch won the meal ticks; encounters so promiscuous that
clubs were a rounding error; seniority pay compounding until 27 businesses
failed; venues that paid 1.07x what they took in. Each was found by running
the town for months and reading the numbers.

So: after any change to the simulation, soak it and check it.

```powershell
.\.venv\Scripts\python.exe scripts\soak.py --days 365 --tag mine   # runs on a copy
.\.venv\Scripts\python.exe scripts\soak_check.py data\soak-mine.db # invariants
.\.venv\Scripts\python.exe scripts\soak_check.py --run 365 --tag mine  # both at once
```

`soak_check.py` asserts what a *living town* should hold — money conserved,
no deficit spiral, employment in band, venues staffed and able to pay their
way, wages not compounding, businesses not churning, the graph having
structure, the town having friction, people content, and the town staying
eventful. A failure names the number that broke it; the probes in
`scripts/_*.py` (untracked, ad hoc) are what you write next to attribute it.

Use `--source` to soak a specific database (both scripts). Pass
`--mortality-scale` to `scripts/soak.py` to accelerate generational turnover.
